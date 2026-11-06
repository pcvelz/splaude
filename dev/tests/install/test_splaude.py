"""install/splaude.py: a bare splaude and splaude setup."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from install import launcher, splaude

MODEL = "unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M"
FACTS = {"chip": "Apple M4 Pro", "memory": 48, "macos": "26.4", "free": 300}
CONFIG = {
    "model": MODEL,
    "name": "Qwen",
    "serve": ["--reasoning", "off"],
    "env": {"MAX_THINKING_TOKENS": "4096"},
}


def option(model=MODEL, label="M", family="Qwen3.8-27B", recommended=False):
    return {
        "label": label,
        "about": "a",
        "model": model,
        "family": family,
        "recommended": recommended,
    }


def answer(options, question="Which model should Splaude run?"):
    """claude -p's completed process answering with options."""
    advice = {"structured_output": {"question": question, "options": options}}
    return mock.Mock(returncode=0, stdout=json.dumps(advice))


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


class ConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = mock.patch.object(
            splaude, "CONFIG", Path(directory.name) / "splaude/config.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_saved_config_loads_back(self):
        splaude.save_config(CONFIG)
        self.assertEqual(splaude.load_config(), CONFIG)

    def test_a_bare_model_gets_its_name_and_splashs_defaults(self):
        splaude.CONFIG.parent.mkdir(parents=True)
        splaude.CONFIG.write_text('{"model": "a/b"}')
        self.assertEqual(
            splaude.load_config(),
            {"model": "a/b", "name": "a/b", "serve": [], "env": {}},
        )

    def test_a_missing_or_broken_config_is_none(self):
        self.assertIsNone(splaude.load_config())
        splaude.CONFIG.parent.mkdir(parents=True)
        for text in (
            "not json",
            "[]",
            '{"serve": []}',
            '{"model": "no-repo"}',
            '{"model": "a/b", "serve": "x"}',
            '{"model": "a/b", "env": {"K": 1}}',
        ):
            with self.subTest(text=text):
                splaude.CONFIG.write_text(text)
                self.assertIsNone(splaude.load_config())


class AdviceTests(unittest.TestCase):
    def ask(self, answers, problems=()):
        """ask_claude against claude -p answering `answers` in turn, the Hub
        check finding problems[model]."""
        completed = [answer(options) for options in answers]
        with (
            mock.patch.object(splaude.subprocess, "run", side_effect=completed) as run,
            mock.patch.object(splaude, "hub_problem", side_effect=dict(problems).get),
        ):
            return splaude.ask_claude("/bin/claude", FACTS), run

    def test_claude_searches_the_web_for_the_schema(self):
        (question, options), run = self.ask([[option(recommended=True)]])
        self.assertEqual(question, "Which model should Splaude run?")
        self.assertEqual([o["model"] for o in options], [MODEL])
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "WebSearch,WebFetch")
        schema = json.loads(argv[argv.index("--json-schema") + 1])
        self.assertEqual(schema, splaude.ADVICE_SCHEMA)
        self.assertEqual(run.call_args.kwargs["timeout"], splaude.ADVICE_TIMEOUT)

    def test_the_prompt_holds_the_mac_the_families_the_rows_and_the_notes(self):
        prompt = splaude.advice_prompt(FACTS)
        self.assertIn("Apple M4 Pro, 48 GB unified memory, macOS 26.4, 300 GB", prompt)
        for family in splaude.families.FAMILIES:
            suffix = "" if family.vision else " (text only)"
            self.assertIn(f"- {family.name}{suffix}\n", prompt)
        self.assertIn(f"{MODEL}, Qwen3.8-27B, 36, 21, ", prompt)
        self.assertIn(splaude.HOUSE_NOTES, prompt)

    def test_a_rejected_model_goes_back_once_with_its_problem(self):
        bad = "fake/Model-GGUF:Q4"
        (_, options), run = self.ask(
            [[option(bad, recommended=True)], [option(recommended=True)]],
            {bad: "no such repository"},
        )
        self.assertEqual([o["model"] for o in options], [MODEL])
        self.assertEqual(run.call_count, 2)
        self.assertIn(f"- {bad}: no such repository", run.call_args.kwargs["input"])

    def test_a_wrong_family_goes_back_too(self):
        (_, options), run = self.ask([[option(family="Llama")], [option()]])
        self.assertEqual(run.call_count, 2)
        self.assertIn("family must be one of", run.call_args.kwargs["input"])
        self.assertEqual(options[0]["family"], "Qwen3.8-27B")

    def test_after_the_second_answer_only_the_valid_options_stay(self):
        bad = "fake/Model-GGUF:Q4"
        (_, options), run = self.ask(
            [[option(bad, recommended=True), option()]] * 2, {bad: "no such repository"}
        )
        self.assertEqual(run.call_count, 2)
        self.assertEqual([o["model"] for o in options], [MODEL])
        self.assertTrue(options[0]["recommended"])

    def test_no_valid_model_twice_is_unsupported(self):
        bad = "fake/Model-GGUF:Q4"
        with self.assertRaises(splaude.Unsupported):
            self.ask([[option(bad)]] * 2, {bad: "no such repository"})

    def test_the_recommended_option_comes_first_and_alone(self):
        options = splaude.rank(
            [
                option("a/A", "A"),
                option("b/B", "B", recommended=True),
                option("c/C", "C", recommended=True),
            ]
        )
        self.assertEqual([o["label"] for o in options], ["B", "A", "C"])
        self.assertEqual([o["recommended"] for o in options], [True, False, False])

    def test_every_malformed_answer_is_unsupported(self):
        for returncode, stdout in (
            (1, ""),
            (0, "not json"),
            (0, json.dumps({"result": "text only"})),
            (0, answer([{"label": "X"}]).stdout),
        ):
            with self.subTest(stdout=stdout), self.assertRaises(splaude.Unsupported):
                splaude.parse_advice(returncode, stdout)


class FamilySettingsTests(unittest.TestCase):
    def test_every_listed_family_is_one_splash_serves(self):
        served = {family.name for family in splaude.families.FAMILIES}
        self.assertLessEqual(set(splaude.FAMILY_SETTINGS), served)

    def test_every_serve_setting_is_one_splaude_serve_takes(self):
        for family, settings in splaude.FAMILY_SETTINGS.items():
            with self.subTest(family=family):
                launcher.parse_args(["serve", "--model", "a/b:Q4", *settings["serve"]])

    def test_reasoning_is_off_for_27b_and_on_for_flash_next(self):
        def reasoning(family):
            serve = splaude.FAMILY_SETTINGS[family]["serve"]
            return serve[serve.index("--reasoning") + 1]

        self.assertEqual(reasoning("Qwen3.8-27B"), "off")
        self.assertEqual(reasoning("Qwen3.8-Flash-Next"), "auto")


class HubCheckTests(unittest.TestCase):
    def check(self, model, files):
        body = json.dumps({"siblings": [{"rfilename": f} for f in files]}).encode()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = body
        with mock.patch.object(
            splaude.urllib.request, "urlopen", return_value=response
        ):
            return splaude.hub_problem(model)

    def test_a_gguf_quant_needs_its_file(self):
        files = ["Qwen3.8-27B-UD-Q4_K_M.gguf", "README.md"]
        self.assertIsNone(self.check(MODEL, files))
        problem = self.check("unsloth/Qwen3.8-27B-GGUF:UD-Q6_K_XL", files)
        self.assertIn("UD-Q6_K_XL", problem)

    def test_an_mlx_repository_needs_its_config(self):
        self.assertIsNone(self.check("mlx-community/Qwen3.8-27B-4bit", ["config.json"]))
        self.assertIsNotNone(self.check("unsloth/Qwen3.8-27B-GGUF", ["x.gguf"]))

    def test_a_missing_repository_is_named(self):
        error = splaude.urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with mock.patch.object(splaude.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(splaude.hub_problem("fake/Model"), "no such repository")


class KeyTests(unittest.TestCase):
    def test_enter_arrives_as_cr_or_as_lf_under_icrnl(self):
        keys = ((b"\r", "enter"), (b"\n", "enter"), (b"\x1b[A", "up"), (b"j", "down"))
        for raw, key in (*keys, (b"x", None)):
            with mock.patch.object(splaude.os, "read", return_value=raw):
                self.assertEqual(splaude._read_key(0), key)
        with (
            mock.patch.object(splaude.os, "read", return_value=b"\x03"),
            self.assertRaises(KeyboardInterrupt),
        ):
            splaude._read_key(0)


class DownloadTests(unittest.TestCase):
    def download(self, lines, returncode=0):
        """download("m") against an installer printing lines: the result,
        the progress reports, the log it left and the Popen call."""
        process = mock.MagicMock(stdout=[line + b"\n" for line in lines])
        process.returncode = returncode
        process.__enter__.return_value = process
        process.__exit__.return_value = False
        reports = []
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(splaude, "SERVE_LOG", Path(directory) / "serve.log"),
            mock.patch.object(
                splaude.subprocess, "Popen", return_value=process
            ) as popen,
        ):
            result = splaude.download("m", lambda d, t, r: reports.append((d, t)))
            log = splaude.SERVE_LOG.read_text()
        return result, reports, log, popen

    def test_progress_adds_up_the_model_and_its_draft(self):
        events = [
            {"event": "download", "repo": "m", "done": 0, "total": 20},
            {"event": "download", "repo": "m", "done": 20, "total": 20},
            {"event": "download", "repo": "d", "done": 5, "total": 10},
        ]
        lines = [b"Fetching 1 file(s)", *(json.dumps(e).encode() for e in events)]
        result, reports, log, popen = self.download(lines)
        self.assertIsNone(result)
        self.assertEqual(reports, [(0, 20), (20, 20), (25, 30)])
        self.assertEqual(log, "Fetching 1 file(s)\n")
        argv = popen.call_args.args[0]
        self.assertEqual(argv[-2:], ["prepare", "--json-progress"])
        self.assertEqual(popen.call_args.kwargs["env"]["HF_HUB_DOWNLOAD_TIMEOUT"], "60")

    def test_a_failed_installer_returns_its_last_error_line(self):
        lines = [b"error: first", b"error: this GGUF stores tensors Splash cannot load"]
        result, _, log, _ = self.download(lines, returncode=1)
        self.assertEqual(result, "this GGUF stores tensors Splash cannot load")
        self.assertIn("error: first\n", log)
        self.assertIn("failed, log:", self.download([], returncode=1)[0])

    def test_the_progress_line_shows_the_bar_the_sizes_and_the_time_left(self):
        line = splaude.progress(13e9, 26e9, 38e6)
        self.assertIn("█" * 10 + "░" * 10, line)
        self.assertIn("13.0 / 26.0 GB", line)
        self.assertIn("38 MB/s", line)
        self.assertIn("about 6 min left", line)
        self.assertNotIn("MB/s", splaude.progress(0, 26e9, 0))


class FetchTests(unittest.TestCase):
    def fetch(self, outcomes):
        """fetch("m") with download() ending in outcomes in turn: the result,
        the attempts, the waits between them and the Download step's last
        state."""
        steps = mock.Mock()
        with (
            mock.patch.object(splaude, "download", side_effect=outcomes) as download,
            mock.patch.object(splaude.time, "sleep") as sleep,
        ):
            result = splaude.fetch("m", steps)
        waits = [call.args[0] for call in sleep.call_args_list]
        return result, download.call_count, waits, steps.set.call_args.args

    def test_a_lost_connection_is_retried_after_a_wait(self):
        outcomes = ["Read timed out.", "Connection reset by peer", None]
        result, attempts, waits, last = self.fetch(outcomes)
        self.assertTrue(result)
        self.assertEqual((attempts, waits), (3, [5, 15]))
        self.assertEqual(last, (0, "x"))

    def test_a_refusal_is_shown_and_not_retried(self):
        refusal = "this GGUF stores tensors Splash cannot load: x type 143"
        result, attempts, _, last = self.fetch([refusal])
        self.assertFalse(result)
        self.assertEqual(attempts, 1)
        self.assertEqual(last, (0, "!", refusal))

    def test_the_last_attempt_gives_up(self):
        result, attempts, waits, last = self.fetch(["timeout"] * 6)
        self.assertFalse(result)
        self.assertEqual((attempts, waits), (6, list(splaude.RETRY_WAITS)))
        self.assertEqual(last, (0, "!", "timeout"))


class InstallTests(unittest.TestCase):
    def install(self, family="Qwen3.8-27B", ready=True, reply="Ready to help."):
        """install() with the download done and Splash mocked: the result,
        the Server and the Server() call."""
        server = mock.Mock(port=8000)
        server.wait_ready.side_effect = (
            [ready] if ready is not None else KeyboardInterrupt
        )
        with (
            quiet(),
            mock.patch.object(splaude, "fetch", return_value=True),
            mock.patch.object(splaude, "Server", return_value=server) as started,
            mock.patch.object(splaude, "first_reply", return_value=reply),
        ):
            result = splaude.install(option(label="Qwen", family=family))
        return result, server, started

    def test_a_tested_model_is_handed_over_running_with_its_familys_settings(self):
        result, server, started = self.install()
        config = {
            "model": MODEL,
            "name": "Qwen",
            **splaude.FAMILY_SETTINGS["Qwen3.8-27B"],
        }
        self.assertEqual(result, (config, server))
        self.assertEqual(started.call_args.args[0], config)
        server.stop.assert_not_called()

    def test_a_family_without_settings_keeps_splashs_defaults(self):
        (config, _), _, _ = self.install(family="Flint")
        self.assertEqual((config["serve"], config["env"]), ([], {}))

    def test_a_splash_that_does_not_start_or_answer_is_stopped(self):
        for ready, reply in ((False, None), (True, None)):
            with self.subTest(ready=ready):
                result, server, _ = self.install(ready=ready, reply=reply)
                self.assertIsNone(result)
                server.stop.assert_called_once()

    def test_an_interrupt_while_splash_starts_stops_it(self):
        with self.assertRaises(KeyboardInterrupt):
            self.install(ready=None)


class RunTests(unittest.TestCase):
    def run_claude(self, config=CONFIG, running=None, ready=True, environ=()):
        """run(config) with Splash and Claude Code mocked: the exit status,
        the output, the Server() call, the Server and the Claude Code call."""
        server = mock.Mock(port=8123)
        server.wait_ready.return_value = ready
        launch = (
            "/bin/claude",
            ["claude"],
            dict(environ, ANTHROPIC_AUTH_TOKEN="local"),
        )
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            mock.patch.dict(os.environ, {"SPLASH_PORT": "8123"}),
            mock.patch.object(splaude, "server_model", return_value=running),
            mock.patch.object(splaude, "Server", return_value=server) as started,
            mock.patch.object(splaude.launcher, "client_launch", return_value=launch),
            mock.patch.object(
                splaude.subprocess, "run", return_value=mock.Mock(returncode=3)
            ) as claude,
        ):
            code = splaude.run(config)
        return code, output.getvalue(), started, server, claude

    def test_splash_is_started_for_the_saved_model_and_stopped_after_claude_code(self):
        code, output, started, server, claude = self.run_claude()
        self.assertEqual(code, 3)
        self.assertEqual(started.call_args.args, (CONFIG, 8123))
        self.assertEqual(claude.call_args.kwargs["executable"], "/bin/claude")
        server.stop.assert_called_once()
        self.assertEqual(output.strip(), "Splaude ran on Qwen")

    def test_the_familys_environment_is_a_default_the_users_own_wins_over(self):
        _, _, _, _, claude = self.run_claude()
        self.assertEqual(claude.call_args.kwargs["env"]["MAX_THINKING_TOKENS"], "4096")
        _, _, _, _, claude = self.run_claude(environ={"MAX_THINKING_TOKENS": "1"})
        self.assertEqual(claude.call_args.kwargs["env"]["MAX_THINKING_TOKENS"], "1")

    def test_a_splash_already_serving_is_used_and_left_running(self):
        code, output, started, _, _ = self.run_claude(running="other/Model")
        self.assertEqual(code, 3)
        started.assert_not_called()
        self.assertEqual(output.strip(), "Splaude ran on other/Model")

    def test_a_splash_that_does_not_start_ends_at_the_manual_installation(self):
        code, output, _, server, claude = self.run_claude(ready=False)
        self.assertEqual(code, 1)
        self.assertIn(splaude.MANUAL_URL, output)
        server.stop.assert_called_once()
        claude.assert_not_called()


class MainTests(unittest.TestCase):
    def main(self, setup=False, config=None, tty=True, **patches):
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            mock.patch.object(splaude, "load_config", return_value=config) as load,
            mock.patch.object(splaude.sys.stdin, "isatty", return_value=tty),
            mock.patch.object(splaude.sys.stdout, "isatty", return_value=tty),
            mock.patch.multiple(splaude, **patches)
            if patches
            else contextlib.nullcontext(),
        ):
            code = splaude.main(setup=setup)
        return code, output.getvalue(), load

    def test_without_a_terminal_it_points_to_the_manual_installation(self):
        code, output, _ = self.main(tty=False)
        self.assertEqual(code, 1)
        self.assertIn(splaude.MANUAL_URL, output)

    def test_with_a_saved_model_it_runs_straight_away(self):
        run = mock.Mock(return_value=0)
        self.assertEqual(self.main(config=CONFIG, run=run)[0], 0)
        run.assert_called_once_with(CONFIG)

    def test_setup_shows_the_menu_even_with_a_saved_model(self):
        menu = mock.Mock(return_value=0)
        _, _, load = self.main(setup=True, menu=menu)
        load.assert_not_called()
        menu.assert_called_once()

    def test_an_unsupported_case_ends_at_the_manual_installation(self):
        menu = mock.Mock(side_effect=splaude.Unsupported("no claude"))
        code, output, _ = self.main(menu=menu)
        self.assertEqual(code, 1)
        self.assertIn("Automatic setup didn't work here.", output)
        self.assertIn(f"{splaude.MANUAL_URL}\n\n  (no claude)", output)

    def test_an_unexpected_error_ends_there_too_and_an_interrupt_is_130(self):
        code, output, _ = self.main(menu=mock.Mock(side_effect=ValueError("x")))
        self.assertEqual((code, splaude.MANUAL_URL in output), (1, True))
        self.assertIn("(ValueError: x)", output)
        code, _, _ = self.main(menu=mock.Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(code, 130)

    def test_bare_launcher_and_setup_open_the_splaude(self):
        with mock.patch.object(splaude, "main", return_value=0) as door:
            self.assertEqual(launcher.main([]), 0)
            self.assertEqual(launcher.main(["setup"]), 0)
        self.assertEqual(
            [call.kwargs for call in door.call_args_list],
            [{"setup": False}, {"setup": True}],
        )
        self.assertEqual(launcher.parse_args(["setup"]).command, "setup")


if __name__ == "__main__":
    unittest.main()
