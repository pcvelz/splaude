"""Splaude's own command: a bare `splaude`, and `splaude setup`.

With a saved model, a bare splaude behaves as claude does: it starts Splash
with that model in the background, opens Claude Code against it without a
word, stops Splash when Claude Code exits, and prints one line naming the
model. Without one, and always for `splaude setup`, it shows a menu whose
first choice, Automatic, sets Splaude up:

1. Claude, through the user's own `claude -p` and a JSON schema, proposes
   two or three models for this Mac in plain words, looking them up on
   Hugging Face. It only advises: the families Splash serves are the hard
   constraint, every model it names is checked to exist before it is
   offered, and an answer with problems goes back to Claude once.
2. Splaude itself downloads the chosen model, starts Splash with the
   family's settings, tests a reply and saves it all to CONFIG.

Every case this flow does not handle (no terminal, Claude Code missing or
logged out, an answer that does not fit the schema, no model that fits)
ends at one message pointing to the README's manual installation.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import time
import tty
import urllib.error
import urllib.request
from pathlib import Path

from server import serve_options

from . import clients, families, launcher, paths

CONFIG = Path(
    os.environ.get("SPLAUDE_CONFIG", "~/.config/splaude/config.json")
).expanduser()
MANUAL_URL = "https://github.com/pcvelz/splaude#manual-installation"
SERVE_LOG = paths.RUNTIME / "splaude-serve.log"
HUB_API = "https://huggingface.co/api/models/"
ADVICE_TOOLS = "WebSearch,WebFetch"
# Claude searches Hugging Face for the advice: minutes, not seconds.
ADVICE_TIMEOUT = 15 * 60
# How long a bare splaude waits for a saved, already downloaded model to load.
READY_TIMEOUT = 30 * 60
# The waits between download attempts after a lost connection. The files an
# attempt finished stay, so the next fetches only what is still missing.
RETRY_WAITS = (5, 15, 30, 60, 120)
# What the installer's error says when the connection failed, not the model.
NETWORK_ERROR = re.compile(
    r"connect|timed out|timeout|network|temporar|reset by peer|resol|ssl| 429| 50[0234]",
    re.IGNORECASE,
)


def _serve(reasoning, **sampling):
    """A serve argv: the reasoning mode, int8 KV and the sampling defaults."""
    options = ["--reasoning", reasoning, "--kv-format", "int8"]
    for name, value in sampling.items():
        options += ["--sampling-default", f"{name}={value}"]
    return options


# The settings Claude Code runs with in our tests on Apple silicon, per
# family: "serve" for Splash, "env" for Claude Code. Saved with the model and
# editable in CONFIG; a family not listed keeps Splash's defaults.
# - Qwen3.8-27B: reasoning off (in no recorded run did thinking rescue a
#   degenerate tool loop), Unsloth's instruct sampling with presence 1.5.
# - Qwen3.8-Flash-Next: reasoning on (at a real loop onset it broke the
#   action loop: 2-3/10 continued against 10/10 with it off), bounded at
#   4096 thinking tokens; Qwen's thinking sampling; the sparse-KV block
#   cache at 1200M (past ~62K tokens of context decode fell ~30% at its
#   floor).
FAMILY_SETTINGS = {
    "Qwen3.8-27B": {
        "serve": _serve(
            "off", temperature=0.7, top_p=0.8, top_k=20, min_p=0, presence_penalty=1.5
        ),
        "env": {},
    },
    "Qwen3.8-Flash-Next": {
        "serve": _serve(
            "auto", temperature=1.0, top_p=0.95, top_k=20, min_p=0, presence_penalty=0
        )
        + ["--sparse-kv-memory", "1200M"],
        "env": {"MAX_THINKING_TOKENS": "4096"},
    },
}
NO_SETTINGS = {"serve": [], "env": {}}

ADVICE_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "options": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "about": {"type": "string"},
                    "model": {"type": "string"},
                    "family": {"type": "string"},
                    "recommended": {"type": "boolean"},
                },
                "required": ["label", "about", "model", "family", "recommended"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["question", "options"],
    "additionalProperties": False,
}

ADVICE_PROMPT = """\
Task: pick 2-3 local models for Claude Code on this Mac, served by Splash.

Mac: {chip}, {memory} GB unified memory, macOS {macos}, {free} GB free disk.

Hard constraint - Splash serves only these families (fine-tunes and
re-quantizations of them included, any uploader):
{families}

ID format:
- GGUF: owner/repo:VARIANT (VARIANT as in the file names), e.g. unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M
- MLX: owner/repo, e.g. mlx-community/Qwen3.8-27B-4bit

Fit:
- Splash gets about two thirds of unified memory on Macs up to 36 GB and three quarters above (Metal's working-set limit): weights plus KV for long context must fit in that
- Qwen3.8-Flash-Next may exceed memory: Splash streams its experts from SSD. Tested on 48 GB; below 48 GB it is untested - do not recommend it there
- download must fit the free disk with 10 GB spare
- 16 GB: nothing runs Claude Code well; offer what loads and say so in about

Community reports (Reddit 2026, weigh them):
- 24 GB: Qwen3.6-35B-A3B at about 3-bit (UD-IQ3_XXS, UD-Q2_K_XL) is what people run, ~20 tok/s; Qwen3.8-27B fits only at 3-bit with ~64K context, 7-11 tok/s
- 32 GB: Qwen3.8-27B Q4_K_M loads but leaves no room for 128K context; IQ4_XS, or ISTA-DASLab GSQ-RCO IQ3 quants, with 8-bit KV do
- Claude Code's first turn is one long prefill: a dense 27B takes minutes on base chips; 35B-A3B prefills 4-6x faster

Verify on Hugging Face that each repo and quant exists today; take sizes from its files.

Known-good starting points (MODEL, NAME, MIN_MEMORY_GB, DOWNLOAD_GB, ABOUT), not exhaustive:
{rows}
{notes}
Output: best first, exactly one recommended.
- label: short model name
- about: max 10 plain words for a non-technical user, comparing the options on this Mac, ending with the download size
- family: the family it belongs to, exactly as named in the list above
- question: "Which model should Splaude run?"
"""

REPAIR_PROMPT = """\
{prompt}
Rejected in your last answer - fix and answer again:
{problems}
"""

# The maintainers' own findings, which a fresh Claude session cannot know:
# advice, never a whitelist. Keep each line something we ran.
HOUSE_NOTES = """\
Empirical evidence - tests of Claude Code on Apple silicon with Splash have shown (weigh it, not a rule):
- Qwen3.8-27B: the most reliable in long tool loops; default when it fits. 48 GB Mac: unsloth UD-Q6_K_XL runs at 262144 context.
- Reliability in long tool loops matters more than raw speed for Claude Code.
- Qwen3.8-Flash-Next: the most capable. 48 GB Mac: unsloth UD-Q4_K_XL with SSD expert streaming passed 9/9 qualification runs. If it does not fit the disk: ISTA-DASLab GSQ-RCO IQ3_S. Avoid UD-IQ4_XS: no quality gain, 30% slower decode.
- GGUF is tested on Splash; MLX variants are not: prefer GGUF of the same model.
- Qwen3.6-35B-A3B: faster, loses the thread sooner in long sessions; second choice.
- Ternary-Bonsai-2: lowest quality; only when nothing else fits.
"""


class Unsupported(Exception):
    """A case the automatic flow leaves to the manual installation."""


# --- config -----------------------------------------------------------------


def load_config():
    """The saved {"model", "name", "serve", "env"}, or None when there is
    none or it is unusable."""
    try:
        saved = json.loads(CONFIG.read_text())
        config = {
            "model": serve_options.parse_model_id(saved["model"]),
            "name": saved.get("name") or saved["model"],
            "serve": saved.get("serve", []),
            "env": saved.get("env", {}),
        }
    except (OSError, ValueError, KeyError, TypeError, argparse.ArgumentTypeError):
        return None
    if not (isinstance(config["serve"], list) and isinstance(config["env"], dict)):
        return None
    values = [config["name"], *config["serve"], *config["env"], *config["env"].values()]
    return config if all(isinstance(value, str) for value in values) else None


def save_config(config):
    """config written to CONFIG, whole or not at all."""
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n")
    temporary.replace(CONFIG)


# --- step 1: Claude advises -------------------------------------------------


def mac_facts():
    """The chip, memory in GB, macOS version and free GB on the models disk,
    measured; SPLAUDE_MEMORY_GB asks what another Mac would be advised."""

    def read(*command):
        return subprocess.run(
            command, capture_output=True, text=True, check=True
        ).stdout.strip()

    disk = next(p for p in (paths.MODELS, *paths.MODELS.parents) if p.exists())
    memory = os.environ.get("SPLAUDE_MEMORY_GB") or round(
        int(read("sysctl", "-n", "hw.memsize")) / 2**30
    )
    return {
        "chip": read("sysctl", "-n", "machdep.cpu.brand_string"),
        "memory": int(memory),
        "macos": read("sw_vers", "-productVersion"),
        "free": shutil.disk_usage(disk).free // 2**30,
    }


def advice_prompt(facts):
    served = "\n".join(
        f"- {family.name}" + ("" if family.vision else " (text only)")
        for family in families.FAMILIES
    )
    suggested = paths.ROOT / "install/suggested-models.tsv"
    rows = [
        line.replace("\t", ", ")
        for line in suggested.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return ADVICE_PROMPT.format(
        **facts, families=served, rows="\n".join(rows), notes=HOUSE_NOTES
    )


def ask_claude(claude, facts):
    """The question and the options Claude proposes for this Mac, each of a
    family Splash serves and found on Hugging Face. An answer with problems
    goes back to Claude once, the problems named. Raises Unsupported."""
    prompt = advice_prompt(facts)
    for attempt in range(2):
        question, options = claude_advice(claude, prompt)
        problems = {o["model"]: option_problem(o) for o in options}
        valid = [o for o in options if problems[o["model"]] is None]
        if len(valid) == len(options) or attempt:
            break
        rejected = "\n".join(f"- {m}: {p}" for m, p in problems.items() if p)
        prompt = REPAIR_PROMPT.format(prompt=prompt, problems=rejected)
    if not valid:
        raise Unsupported("Claude proposed no model that Splash can serve")
    return question, rank(valid)


def claude_advice(claude, prompt):
    """Claude's structured answer to prompt, searching the web as it needs.
    Raises Unsupported."""
    # An empty directory: no project of the user's shapes the answer. The
    # prompt goes on stdin: --tools and --allowedTools take every argument
    # after them.
    tools = ["--tools", ADVICE_TOOLS, "--allowedTools", ADVICE_TOOLS]
    output = ["--output-format", "json", "--json-schema", json.dumps(ADVICE_SCHEMA)]
    command = [claude, "-p", "--no-session-persistence", *tools, *output]
    with tempfile.TemporaryDirectory() as empty:
        try:
            result = subprocess.run(
                command,
                cwd=empty,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=ADVICE_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Unsupported(f"Claude did not answer: {error}") from None
    return parse_advice(result.returncode, result.stdout)


def parse_advice(returncode, stdout):
    """The question and the well-formed options of a claude -p answer.
    Raises Unsupported."""
    if returncode:
        raise Unsupported("Claude Code could not answer (is it logged in?)")
    try:
        advice = json.loads(stdout)["structured_output"]
        question, options = str(advice["question"]), list(advice["options"])
    except (ValueError, KeyError, TypeError):
        raise Unsupported("Claude's answer was not in the expected shape") from None
    options = [
        o
        for o in options
        if isinstance(o, dict)
        and all(isinstance(o.get(key), str) for key in ("model", "label", "about"))
    ]
    if not options:
        raise Unsupported("Claude's answer named no model")
    return question, options


def option_problem(option):
    """Why an option Claude proposed cannot be offered, or None."""
    served = [family.name for family in families.FAMILIES]
    if option.get("family") not in served:
        return f"family must be one of: {', '.join(served)}"
    return hub_problem(option["model"])


def hub_problem(model):
    """Why model is not a Splash model ID that exists on Hugging Face, or
    None. A metadata request; nothing is downloaded."""
    try:
        serve_options.parse_model_id(model)
    except argparse.ArgumentTypeError as error:
        return str(error)
    repo, _, variant = model.partition(":")
    try:
        with urllib.request.urlopen(HUB_API + repo, timeout=30) as response:
            siblings = json.loads(response.read())["siblings"]
            files = [sibling.get("rfilename", "") for sibling in siblings]
    except urllib.error.HTTPError as error:
        if error.code in (401, 404):
            return "no such repository"
        return f"Hugging Face said {error.code}"
    except (OSError, ValueError, KeyError, TypeError) as error:
        return f"could not reach Hugging Face: {error}"
    if variant and not any(
        f.endswith(".gguf") and variant.lower() in f.lower() for f in files
    ):
        return f"no GGUF file of quant {variant} in the repository"
    if not variant and "config.json" not in files:
        return "not an MLX model repository (no config.json); a GGUF one needs :VARIANT"
    return None


def rank(options):
    """The options with exactly one recommended, and it first."""
    first = next((o for o in options if o.get("recommended")), options[0])
    ranked = [first, *(o for o in options if o is not first)]
    for option in ranked:
        option["recommended"] = option is first
    return ranked


# --- the menu ---------------------------------------------------------------


def _read_key(stream):
    key = os.read(stream, 3).decode(errors="ignore")
    if key in ("\x03", "q"):
        raise KeyboardInterrupt
    if key in ("\x1b[A", "k"):
        return "up"
    if key in ("\x1b[B", "j"):
        return "down"
    # Enter: ICRNL, which cbreak mode keeps, turns the terminal's \r into \n.
    return "enter" if key in ("\r", "\n") else None


def choose(heading, options):
    """The index of the option picked with the arrow keys and Enter; options
    are (label, about) pairs. The menu collapses to the heading and the pick."""
    width = max(len(label) for label, _ in options)
    selected = 0
    print(f"  {heading}\n", flush=True)
    stream = sys.stdin.fileno()
    saved = termios.tcgetattr(stream)
    try:
        tty.setcbreak(stream)
        sys.stdout.write("\x1b[?25l")
        while True:
            for index, (label, about) in enumerate(options):
                marker = ">" if index == selected else " "
                line = f"  {marker} {label.ljust(width)}   {about}".rstrip()
                if index == selected:
                    line = f"\x1b[1m{line}\x1b[0m"
                sys.stdout.write(f"\x1b[2K{line}\n")
            sys.stdout.write(
                "\x1b[2K\n\x1b[2K  \x1b[2mUp/Down to choose, Enter to confirm\x1b[0m\n"
            )
            sys.stdout.flush()
            key = _read_key(stream)
            if key == "enter":
                sys.stdout.write(f"\x1b[{len(options) + 4}A\x1b[J")
                sys.stdout.write(
                    f"  {heading}  \x1b[1m{options[selected][0]}\x1b[0m\n\n"
                )
                return selected
            selected = (selected + {"up": -1, "down": 1}.get(key, 0)) % len(options)
            sys.stdout.write(f"\x1b[{len(options) + 2}A")
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()
        termios.tcsetattr(stream, termios.TCSADRAIN, saved)


def manual(reason=None):
    """The way out: the README's manual installation; 1 when a reason is
    named, after a failure."""
    if reason is None:
        print(f"  Manual steps: {MANUAL_URL}")
        return 0
    print("  Automatic setup didn't work here.")
    print(f"  Follow the manual steps: {MANUAL_URL}")
    print(f"\n  ({reason})")
    return 1


# --- the server -------------------------------------------------------------


def splash_port():
    return int(os.environ.get("SPLASH_PORT") or serve_options.DEFAULT_PORT)


def server_model(port):
    """The model a ready Splash on port serves, or None."""
    try:
        listing = launcher._request_json("/v1/models", port=port)
    except launcher.LauncherError:
        return None
    models = listing.get("data") if isinstance(listing, dict) else None
    first = models[0] if isinstance(models, list) and models else None
    if isinstance(first, dict) and first.get("owned_by") == "splash":
        return first.get("id")
    return None


def hub_environment():
    """The environment with Hub timeouts for slow connections: the 10 s
    defaults give up on one that a longer wait carries through."""
    environment = dict(os.environ)
    environment.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    environment.setdefault("HF_HUB_ETAG_TIMEOUT", "60")
    return environment


class Server:
    """Splash started in the background: `splaude serve` in its own session,
    so Ctrl+C in Claude Code never reaches it, its output in SERVE_LOG."""

    def __init__(self, config, port):
        SERVE_LOG.parent.mkdir(parents=True, exist_ok=True)
        self.port = port
        self._log = SERVE_LOG.open("ab")
        serve = ["serve", "--model", config["model"], "--port", str(port)]
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(launcher.__file__), *serve, *config["serve"]],
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=hub_environment(),
        )

    def wait_ready(self, timeout=None):
        """True once it serves; False if it exits or the timeout passes."""
        deadline = time.monotonic() + (timeout or float("inf"))
        while time.monotonic() < deadline and self.process.poll() is None:
            if server_model(self.port) is not None:
                return True
            time.sleep(0.5)
        return False

    def stop(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._log.close()


def first_reply(port, model):
    """The text of a short reply, or None when there is none."""
    body = json.dumps(
        {
            "model": model,
            "max_tokens": 64,
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "user", "content": "Reply with exactly: Ready to help."}
            ],
        }
    ).encode()
    request = urllib.request.Request(
        launcher._base_url(port) + "/v1/messages",
        data=body,
        headers={"content-type": "application/json", "x-api-key": "local"},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            reply = json.loads(response.read())
    except (OSError, ValueError):
        return None
    blocks = [block for block in reply.get("content", []) if isinstance(block, dict)]
    return "".join(block.get("text", "") for block in blocks).strip() or None


# --- running ----------------------------------------------------------------


def run(config, server=None):
    """Claude Code against Splash, started here for config's model unless
    one serves already; one line naming the model once Claude Code exits."""
    port = splash_port()
    name = config["name"]
    if server is None:
        running = server_model(port)
        if running is None:
            server = Server(config, port)
            if not server.wait_ready(READY_TIMEOUT):
                server.stop()
                return manual(f"Splash did not start; its log: {SERVE_LOG}")
        elif running != config["model"]:
            name = running
    try:
        args = argparse.Namespace(
            command="claude", port=port, explicit_port=False, client_args=[]
        )
        try:
            path, command, environment = launcher.client_launch(args, quiet=True)
        except (launcher.LauncherError, clients.ClientError) as error:
            return manual(str(error))
        # The family's Claude Code settings; a value the user set wins.
        environment = {**config["env"], **environment}
        # Claude Code takes Ctrl+C itself; it must not end Splaude meanwhile.
        # A handler, unlike SIG_IGN, is reset for the program it starts.
        stops = (signal.SIGINT, signal.SIGTERM)
        handlers = {number: signal.signal(number, lambda *_: None) for number in stops}
        try:
            code = subprocess.run(command, executable=path, env=environment).returncode
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)
    finally:
        if server is not None:
            server.stop()
    print(f"Splaude ran on {name}")
    return code


# --- step 2: Splaude installs -----------------------------------------------


class Checklist:
    """Step lines redrawn in place: '[ ] name  detail', marked ' ', 'x' or '!'."""

    def __init__(self, names):
        self.names = names
        self.steps = [(" ", "")] * len(names)
        self.drawn = False
        self.draw()

    def set(self, index, mark, detail=""):
        self.steps[index] = (mark, detail)
        self.draw()

    def draw(self):
        if self.drawn:
            sys.stdout.write(f"\x1b[{len(self.names)}A")
        self.drawn = True
        width = max(map(len, self.names))
        columns = shutil.get_terminal_size().columns
        for name, (mark, detail) in zip(self.names, self.steps):
            # Cut at the terminal's edge: a wrapped line would break the redraw.
            line = f"  [{mark}] {name.ljust(width)}  {detail}"[:columns]
            sys.stdout.write(f"\x1b[2K{line}\n")
        sys.stdout.flush()


def progress(done, total, rate):
    """'███████░░░░░░░░░░░░░  14.2 / 26.0 GB   38 MB/s   about 5 min left'"""
    filled = round(20 * done / total) if total else 0
    text = (
        f"{'█' * filled}{'░' * (20 - filled)}  {done / 1e9:.1f} / {total / 1e9:.1f} GB"
    )
    if rate > 0:
        minutes = max(1, round((total - done) / rate / 60))
        text += f"   {rate / 1e6:.0f} MB/s   about {minutes} min left"
    return text


def download(model, report):
    """model installed by Splash's own installer, report(done, total, rate)
    called as its JSON progress arrives. None once it is installed, else
    the installer's error. The model and its draft report one after the
    other: their totals add up as they appear."""
    installer = [str(paths.PYTHON), "-u", str(paths.ROOT / "install/models.py")]
    selection = ["--models", str(paths.MODELS), "--model", model]
    command = [*installer, *selection, "prepare", "--json-progress"]
    repos, first, error = {}, None, f"failed, log: {SERVE_LOG}"
    with (
        SERVE_LOG.open("ab") as log,
        subprocess.Popen(
            command,
            cwd=paths.ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=hub_environment(),
        ) as process,
    ):
        for raw in process.stdout:
            try:
                event = json.loads(raw)
                repos[event["repo"]] = (event["done"], event["total"])
            except (ValueError, KeyError, TypeError):
                log.write(raw)
                if raw.startswith(b"error: "):
                    error = raw[7:].decode(errors="replace").strip()
                continue
            done = sum(done for done, _ in repos.values())
            total = sum(total for _, total in repos.values())
            first = first or (time.monotonic(), done)
            elapsed = time.monotonic() - first[0]
            report(done, total, (done - first[1]) / elapsed if elapsed else 0)
    return None if process.returncode == 0 else error


def fetch(model, steps):
    """download(model) on the Download step, again after a lost connection.
    A refusal (a quant this Splash cannot load) is not retried: another
    model is the way out."""
    steps.set(0, " ", progress(0, 0, 0))
    for wait in (*RETRY_WAITS, None):
        error = download(
            model,
            lambda done, total, rate: steps.set(0, " ", progress(done, total, rate)),
        )
        if error is None:
            steps.set(0, "x")
            return True
        if wait is None or not NETWORK_ERROR.search(error):
            steps.set(0, "!", error)
            return False
        steps.set(0, " ", f"connection lost, retrying in {wait} s")
        time.sleep(wait)


def install(option):
    """Download, start and test the chosen model. Its config and the ready
    Server, or None when a step failed."""
    print(f"  Setting up {option['label']}\n")
    steps = Checklist(["Download", "Start Splash", "Test a reply"])
    if not fetch(option["model"], steps):
        return None
    settings = FAMILY_SETTINGS.get(option["family"], NO_SETTINGS)
    config = {"model": option["model"], "name": option["label"], **settings}
    server = Server(config, splash_port())
    reply = None
    try:
        steps.set(1, " ", "...")
        if not server.wait_ready():
            steps.set(1, "!", f"failed, log: {SERVE_LOG}")
        else:
            steps.set(1, "x")
            steps.set(2, " ", "...")
            reply = first_reply(server.port, config["model"])
            if reply is None:
                steps.set(2, "!", f"no reply, log: {SERVE_LOG}")
            else:
                steps.set(2, "x", f'"{reply[:40]}"')
    finally:
        if reply is None:
            server.stop()
    print()
    return None if reply is None else (config, server)


def automatic():
    """The menu's first choice: Claude advises, the user picks, Splaude
    installs, saves and starts."""
    print("  Claude compares the models that run on your Mac.")
    print("  This can take a few minutes and uses your normal Claude account.\n")
    input("  Press Enter to continue ")
    print()
    # The engine's own check refuses an unsupported Mac before any advice or
    # download. serve makes the runtime folder before its build lock; so must this.
    paths.RUNTIME.mkdir(parents=True, exist_ok=True)
    launcher._ensure_runtime()
    claude = shutil.which("claude")
    if claude is None:
        raise Unsupported("Claude Code is not installed")
    try:
        facts = mac_facts()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise Unsupported(f"could not read this Mac: {error}") from None
    print(
        f"  Your Mac: {facts['chip']}, {facts['memory']} GB memory, "
        f"{facts['free']} GB free disk."
    )
    print("  Claude is looking at Hugging Face...\n", flush=True)
    question, options = ask_claude(claude, facts)
    labels = [
        (o["label"] + (" (recommended)" if o["recommended"] else ""), o["about"])
        for o in options
    ]
    retry = [("Try again", ""), ("Pick another model", "")]
    installed = None
    while installed is None:
        option = options[choose(question, labels)]
        installed = install(option)
        while installed is None and choose("What now?", retry) == 0:
            installed = install(option)
    config, server = installed
    save_config(config)
    print("  You're ready. From now on, just run: splaude\n")
    start = False
    try:
        start = 0 == choose(
            f"Start a Claude Code session now, on your local Splash with {config['name']}?",
            [("Yes", ""), ("Not now", "")],
        )
    finally:
        if not start:
            server.stop()
    return run(config, server) if start else 0


def menu():
    print("\n  Splaude - Claude Code on your own Mac\n")
    while True:
        picked = choose(
            "How do you want to set up?",
            [("Automatic (recommended)", ""), ("More options", "")],
        )
        if picked == 0:
            return automatic()
        picked = choose(
            "More options",
            [
                ("Manual setup", "show the steps to do it yourself"),
                ("Start Claude Code", "already set up? jump straight in"),
                ("Back", ""),
            ],
        )
        if picked == 0:
            return manual()
        if picked == 1:
            config = load_config()
            if config is None and (running := server_model(splash_port())):
                config = {"model": running, "name": running, **NO_SETTINGS}
            return run(config) if config else manual("Splaude is not set up yet")


def main(setup=False):
    """The exit status of a bare splaude, or of splaude setup."""
    try:
        config = None if setup else load_config()
        if config is not None:
            return run(config)
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return manual("no interactive terminal")
        return menu()
    except Unsupported as reason:
        return manual(str(reason))
    except (KeyboardInterrupt, EOFError):
        print()
        return 130
    except Exception as error:
        # Every other failure ends where the unsupported cases do.
        return manual(f"{type(error).__name__}: {error}")
