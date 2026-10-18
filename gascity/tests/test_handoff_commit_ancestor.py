"""handoff-commit-ancestor.sh: the fix-pass hand-off gate (gc-gef2p8).

Reviewer-first: a fix pass hands a local commit to the re-review, and CI runs
once at publish. The gate passes when the recorded commit is on the recorded
work branch. Every case runs the real script as a subprocess with `gc`
replaced by a stub that serves canned bead JSON, against a real throwaway git
repository.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

from test_pr_ci_green import GC_STUB

PACK_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = PACK_ROOT / "assets" / "scripts" / "checks" / "handoff-commit-ancestor.sh"
BRANCH = "fix/gcas-abc123-widgets"
NO_VERDICT = 75


class HandoffCommitAncestorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = pathlib.Path(self._tmp.name).resolve()
        self.bin, self.beads, self.repo, self.city = tmp / "bin", tmp / "beads", tmp / "repo", tmp / "city"
        self.origin = tmp / "origin.git"
        for directory in (self.bin, self.beads, self.repo, self.city):
            directory.mkdir()
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        stub = self.bin / "gc"
        stub.write_text(GC_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.git("init", "-q", "-b", "main")
        self.commit_file("README.md", "base\n", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", BRANCH)
        self.first = self.commit_file("widget.py", "print('widget')\n", "widget [skip ci]")
        # The fix pass made its one checkpoint push before the gate runs.
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-q", "origin", "main", BRANCH)
        self.fix_loop()

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()

    def commit_file(self, name: str, text: str, message: str) -> str:
        (self.repo / name).write_text(text, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def bead(self, bead_id: str, metadata: dict) -> None:
        (self.beads / f"{bead_id}.json").write_text(json.dumps([{"id": bead_id, "metadata": metadata}]), encoding="utf-8")

    def fix_loop(self, *, commit: str | None = None, branch: str | None = BRANCH, push: str = "true", open_pr: str = "true", work_dir: str | None = None) -> None:
        """A standalone fix-loop root; the apply step's `gc.root_bead_id` points at it."""
        root = {"gc.var.push": push, "gc.var.open_pr": open_pr, "gc.work_dir": work_dir if work_dir is not None else str(self.repo)}
        if commit is not None:
            root["gc.build.handoff_commit"] = commit
        if branch is not None:
            root["gc.build.handoff_branch"] = branch
        self.bead("step", {"gc.root_bead_id": "root"})
        self.bead("root", root)

    def run_gate(self, *, bead: str | None = "step", env: dict | None = None) -> subprocess.CompletedProcess:
        full_env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": os.environ.get("HOME", str(self.city)),
            "BD_SHOW_DIR": str(self.beads),
            "HANDOFF_ANCESTOR_RETRY_SLEEP_SECONDS": "0.05",
            **(env or {}),
        }
        if bead is not None:
            full_env["GC_BEAD_ID"] = bead
        return subprocess.run([str(SCRIPT)], env=full_env, cwd=str(self.city), capture_output=True, text=True, check=False, timeout=60)

    def assert_fail(self, result: subprocess.CompletedProcess, *fragments: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("handoff-commit-ancestor: FAIL", result.stderr)
        self.assertNotIn("PASS", result.stdout)
        for fragment in fragments:
            self.assertIn(fragment, result.stderr)

    def test_script_is_executable_and_valid_bash(self) -> None:
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_the_branch_head_passes(self) -> None:
        self.fix_loop(commit=self.first)
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("handoff-commit-ancestor: PASS", result.stdout)
        self.assertIn(self.first[:12], result.stdout)
        self.assertIn("CI deferred to publish", result.stdout)

    def test_a_commit_below_the_branch_head_passes(self) -> None:
        self.commit_file("widget.py", "print('widget v2')\n", "tweak [skip ci]")
        self.fix_loop(commit=self.first)
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("handoff-commit-ancestor: PASS", result.stdout)

    def test_the_gate_reads_a_branch_that_exists_only_as_a_remote_tracking_ref(self) -> None:
        self.git("update-ref", f"refs/remotes/origin/{BRANCH}", self.first)
        self.git("checkout", "-q", "main")
        self.git("branch", "-q", "-D", BRANCH)
        self.fix_loop(commit=self.first)
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("handoff-commit-ancestor: PASS", result.stdout)

    def test_the_repository_falls_back_to_the_working_directory_environment(self) -> None:
        self.fix_loop(commit=self.first, work_dir="")
        result = self.run_gate(env={"GC_WORK_DIR": str(self.repo)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_commit_on_another_branch_fails_and_names_both(self) -> None:
        self.git("checkout", "-q", "-b", "other", self.base)
        other = self.commit_file("other.py", "x = 1\n", "other work")
        self.git("checkout", "-q", BRANCH)
        self.fix_loop(commit=other)
        result = self.run_gate()
        self.assert_fail(result, other[:12], "not an ancestor", BRANCH)

    def test_a_commit_the_repository_does_not_have_fails(self) -> None:
        self.fix_loop(commit="d" * 40)
        result = self.run_gate()
        self.assert_fail(result, "does not exist", "dddddddddddd")

    def test_a_value_that_is_not_a_sha_fails(self) -> None:
        self.fix_loop(commit="not-a-sha")
        self.assert_fail(self.run_gate(), "not a commit sha")

    def test_a_missing_branch_fails(self) -> None:
        self.fix_loop(commit=self.first, branch="no/such-branch")
        self.assert_fail(self.run_gate(), "no/such-branch", "does not exist")

    def test_an_unrecorded_branch_fails(self) -> None:
        self.fix_loop(commit=self.first, branch=None)
        self.assert_fail(self.run_gate(), "gc.build.handoff_branch is not recorded")

    def test_nothing_recorded_in_a_publishing_workflow_fails_with_the_record_command(self) -> None:
        self.fix_loop()
        self.assert_fail(self.run_gate(), "no handoff commit is recorded", "gc.build.handoff_commit")

    def test_nothing_recorded_without_publishing_intent_is_an_explicit_skip(self) -> None:
        self.fix_loop(push="false", open_pr="false")
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("handoff-commit-ancestor: skipped: no publishing intent", result.stdout)
        self.assertNotIn("PASS", result.stdout)

    def test_no_bead_id_fails(self) -> None:
        self.assert_fail(self.run_gate(bead=None), "GC_BEAD_ID is required")

    def test_an_unreachable_bead_store_is_no_verdict_not_a_failed_hand_off(self) -> None:
        (self.beads / "root.json").unlink()
        (self.beads / "root.error").write_text("dolt circuit breaker is open\n", encoding="utf-8")
        result = self.run_gate()
        self.assertEqual(result.returncode, NO_VERDICT, result.stdout + result.stderr)
        self.assertIn("INFRA no verdict", result.stderr)
        self.assertNotIn("FAIL", result.stderr)

    # Publishing workflows (push and open_pr both true): the commit must be a
    # tagged checkpoint, on origin, on a branch that is not the default one.

    def test_every_skip_ci_spelling_passes(self) -> None:
        for message in (
            "fix [ci skip]",
            "fix [no ci]",
            "fix [skip actions]",
            "fix [actions skip]",
            "fix\n\nbody text\n\nskip-checks: true",
        ):
            with self.subTest(message=message):
                commit = self.commit_file("widget.py", f"print({message!r})\n", message)
                self.git("push", "-q", "origin", BRANCH)
                self.fix_loop(commit=commit)
                result = self.run_gate()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("handoff-commit-ancestor: PASS", result.stdout)

    def test_a_head_commit_without_a_skip_ci_tag_fails(self) -> None:
        commit = self.commit_file("widget.py", "print('untagged')\n", "R18 WI-3: chips")
        self.git("push", "-q", "origin", BRANCH)
        self.fix_loop(commit=commit)
        self.assert_fail(self.run_gate(), "head commit carries no skip-ci tag", commit[:12])

    def test_a_tag_in_the_body_of_another_word_is_not_a_tag(self) -> None:
        commit = self.commit_file("widget.py", "print('near miss')\n", "fix: skip ci handling (no tag here)")
        self.git("push", "-q", "origin", BRANCH)
        self.fix_loop(commit=commit)
        self.assert_fail(self.run_gate(), "no skip-ci tag")

    def test_a_commit_that_was_never_pushed_fails_as_not_on_origin(self) -> None:
        commit = self.commit_file("widget.py", "print('local only')\n", "local only [skip ci]")
        self.fix_loop(commit=commit)
        self.assert_fail(self.run_gate(), "checkpoint not on origin", commit[:12], f"origin/{BRANCH}")

    def test_a_stale_remote_tracking_ref_does_not_hide_a_missing_push(self) -> None:
        commit = self.commit_file("widget.py", "print('stale ref')\n", "stale [skip ci]")
        self.git("update-ref", f"refs/remotes/origin/{BRANCH}", commit)  # what this clone believes; origin disagrees
        self.fix_loop(commit=commit)
        self.assert_fail(self.run_gate(), "checkpoint not on origin")

    def test_a_branch_that_does_not_exist_on_origin_fails_as_not_on_origin(self) -> None:
        self.git("checkout", "-q", "-b", "fix/never-pushed")
        commit = self.commit_file("widget.py", "print('new branch')\n", "new branch [skip ci]")
        self.fix_loop(commit=commit, branch="fix/never-pushed")
        self.assert_fail(self.run_gate(), "checkpoint not on origin", "fix/never-pushed")

    def test_an_unreachable_origin_fails_as_not_on_origin(self) -> None:
        self.git("remote", "set-url", "origin", str(self.origin) + ".missing")
        self.fix_loop(commit=self.first)
        self.assert_fail(self.run_gate(), "checkpoint not on origin")

    def test_a_handoff_on_the_default_branch_fails(self) -> None:
        self.git("checkout", "-q", "main")
        commit = self.commit_file("main.py", "x = 1\n", "straight to main [skip ci]")
        self.git("push", "-q", "origin", "main")
        self.fix_loop(commit=commit, branch="main")
        self.assert_fail(self.run_gate(), "default branch", "main")

    def test_the_default_branch_is_read_from_origin_head_when_it_is_set_locally(self) -> None:
        self.git("push", "-q", "origin", f"{BRANCH}:trunk")
        self.git("remote", "set-head", "origin", "trunk")
        self.fix_loop(commit=self.first, branch="trunk")
        self.git("branch", "-q", "trunk", self.first)
        self.assert_fail(self.run_gate(), "default branch", "trunk")

    def test_a_non_default_branch_named_like_one_still_passes(self) -> None:
        self.git("push", "-q", "origin", f"{BRANCH}:main-fixes")
        self.git("branch", "-q", "main-fixes", self.first)
        self.fix_loop(commit=self.first, branch="main-fixes")
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_workflow_that_does_not_publish_skips_the_tag_origin_and_branch_checks(self) -> None:
        self.git("checkout", "-q", "main")
        commit = self.commit_file("main.py", "x = 1\n", "untagged and unpushed")
        self.fix_loop(commit=commit, branch="main", push="false", open_pr="false")
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("handoff-commit-ancestor: PASS", result.stdout)

    def test_each_refusal_is_one_line(self) -> None:
        commit = self.commit_file("widget.py", "print('untagged')\n", "no tag")
        self.fix_loop(commit=commit)
        result = self.run_gate()
        self.assertEqual(len([line for line in result.stderr.splitlines() if line.strip()]), 1, result.stderr)

    def test_the_gate_never_calls_gh(self) -> None:
        self.fix_loop(commit=self.first)
        # No gh on PATH at all: the gate is local.
        result = self.run_gate(env={"PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin{os.pathsep}/opt/homebrew/bin"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
