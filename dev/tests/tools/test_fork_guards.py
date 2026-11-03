"""The fork's push guards: .githooks/pre-push and dev/tools/check_fork_identity.sh.

Each test builds a throwaway repository with the guard copied in, so the
checks run against known history instead of this checkout's.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PRE_PUSH = ROOT / ".githooks" / "pre-push"
IDENTITY = ROOT / "dev" / "tools" / "check_fork_identity.sh"
ZERO = "0" * 40
FORK_URL = "https://github.com/pcvelz/splaude"


class Repo:
    def __init__(self, path):
        self.path = path
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t.local")
        self.git("config", "user.name", "test")

    def git(self, *args):
        result = subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)}: {result.stderr}")
        return result.stdout.strip()

    def commit(self, name, text="x\n"):
        file = self.path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
        self.git("add", name)
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def merge(self, other, message):
        self.git("merge", "-q", "--no-ff", "-m", message, other)
        return self.git("rev-parse", "HEAD")


def install_guards(repo, verifier_exit):
    """The real pre-push and identity check, and a stub merge verifier that
    records each call and exits with `verifier_exit`."""
    (repo.path / ".githooks").mkdir(exist_ok=True)
    shutil.copy(PRE_PUSH, repo.path / ".githooks" / "pre-push")
    tools = repo.path / "dev" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    shutil.copy(IDENTITY, tools / "check_fork_identity.sh")
    (tools / "verify_upstream_merge.sh").write_text(
        f'echo "$1" >> "{repo.path}/verified"\nexit {verifier_exit}\n'
    )


def push(repo, local_oid, remote_oid=ZERO):
    stdin = f"refs/heads/main {local_oid} refs/heads/main {remote_oid}\n"
    return subprocess.run(
        ["sh", ".githooks/pre-push", "origin", FORK_URL],
        cwd=repo.path,
        input=stdin,
        capture_output=True,
        text=True,
    )


class PrePushTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = Repo(self.tmp)
        self.repo.commit("base.txt")

    def upstream_with_merge(self):
        """upstream/main ending in upstream's own merge of a topic branch."""
        self.repo.git("checkout", "-q", "-b", "topic")
        self.repo.commit("topic.txt")
        self.repo.git("checkout", "-q", "main")
        self.repo.commit("upstream-main.txt")
        merge = self.repo.merge("topic", "Merge pull request #1 from someone/topic")
        self.repo.git("update-ref", "refs/remotes/upstream/main", merge)
        return merge

    def test_upstream_own_merge_carried_by_a_rebase_is_not_verified(self):
        self.upstream_with_merge()
        tip = self.repo.commit("fork-patch.txt")
        install_guards(self.repo, verifier_exit=1)
        result = push(self.repo, tip)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.tmp / "verified").exists())

    def test_our_sync_merge_of_upstream_is_still_verified(self):
        self.repo.git("checkout", "-q", "-b", "fork")
        self.repo.commit("fork-patch.txt")
        self.repo.git("checkout", "-q", "main")
        upstream_tip = self.repo.commit("upstream-change.txt")
        self.repo.git("update-ref", "refs/remotes/upstream/main", upstream_tip)
        self.repo.git("checkout", "-q", "fork")
        sync = self.repo.merge(upstream_tip, "Merge upstream main")
        install_guards(self.repo, verifier_exit=1)
        result = push(self.repo, sync)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual((self.tmp / "verified").read_text().split(), [sync])

    def test_push_to_upstream_is_refused(self):
        tip = self.repo.git("rev-parse", "HEAD")
        install_guards(self.repo, verifier_exit=0)
        result = subprocess.run(
            [
                "sh",
                ".githooks/pre-push",
                "upstream",
                "https://github.com/incoai/splash",
            ],
            cwd=self.tmp,
            input=f"refs/heads/main {tip} refs/heads/main {ZERO}\n",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("is upstream", result.stderr)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = Repo(self.tmp)
        install_guards(self.repo, verifier_exit=0)
        self.repo.git("add", ".githooks", "dev")
        self.repo.git("commit", "-q", "-m", "guards")

    def scan(self):
        return subprocess.run(
            ["bash", "dev/tools/check_fork_identity.sh", "HEAD"],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )

    def test_the_guards_naming_upstream_do_not_trip_the_scan(self):
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_an_upstream_install_command_still_trips_the_scan(self):
        self.repo.commit("README.md", "brew install incoai/tap/splash\n")
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("README.md", result.stdout)


if __name__ == "__main__":
    unittest.main()
