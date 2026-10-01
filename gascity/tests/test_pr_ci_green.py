"""pr-ci-green.sh: the CI-green handoff gate (gc-68exu).

Every case runs the real script as a subprocess with `gh` and `gc` replaced
by stubs that serve canned JSON, against a real throwaway git repository.
"""

from __future__ import annotations

import json
import os
import pathlib
import pwd
import re
import subprocess
import tempfile
import unittest

PACK_ROOT = pathlib.Path(__file__).resolve().parents[1]
CHECKS = PACK_ROOT / "assets" / "scripts" / "checks"
SCRIPT = CHECKS / "pr-ci-green.sh"
HANDOFF_SCRIPT = CHECKS / "implementation-handoff-valid.sh"
REPO = "acme/widgets"
BRANCH = "feat/gcas-abc123-widgets"

GH_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
dir="${GH_STUB_DIR:?}"
echo "$*" >>"$dir/calls.log"
if [ "${1:-}" = "auth" ]; then
  if [ -f "$dir/signed-out" ]; then echo "no oauth token" >&2; exit 1; fi
  if [ -f "$dir/signed-in-home" ] && [ "$HOME" != "$(cat "$dir/signed-in-home")" ]; then
    echo "no oauth token" >&2; exit 1
  fi
  echo "gho_stub"; exit 0
fi
[ "${1:-}" = "api" ] || { echo "stub gh: unsupported: $*" >&2; exit 2; }
key="$(printf '%s' "$2" | tr -c 'A-Za-z0-9._-' '_')"
if [ -f "$dir/$key.json" ]; then cat "$dir/$key.json"; exit 0; fi
if [ -f "$dir/$key.error" ]; then cat "$dir/$key.error" >&2; exit 1; fi
echo '{"message":"Not Found"}'
echo "gh: Not Found (HTTP 404)" >&2
exit 1
"""

GC_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "bd" ] && [ "${2:-}" = "show" ] && [ -f "${BD_SHOW_DIR:?}/$3.json" ]; then
  cat "$BD_SHOW_DIR/$3.json"; exit 0
fi
echo "stub gc: unsupported: $*" >&2
exit 1
"""


def route(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", path)


def check_run(name: str, status: str = "completed", conclusion: str | None = "success") -> dict:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://github.com/{REPO}/actions/runs/1/job/{abs(hash(name)) % 100000}",
    }


class Fixture:
    """One temp dir holding a git repo, bead JSON, gh routes and the stub binaries."""

    def __init__(self, tmp: pathlib.Path, *, origin: str = f"https://github.com/{REPO}.git") -> None:
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.gh_dir = tmp / "gh"
        self.beads = tmp / "beads"
        self.city = tmp / "city"
        self.repo = tmp / "repo"
        for directory in (self.bin, self.gh_dir, self.beads, self.city, self.repo):
            directory.mkdir()
        for name, text in (("gh", GH_STUB), ("gc", GC_STUB)):
            stub = self.bin / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        self.git("init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", BRANCH)
        (self.repo / "widget.py").write_text("print('widget')\n", encoding="utf-8")
        self.git("add", "widget.py")
        self.git("commit", "-q", "-m", "widget")
        self.head = self.git("rev-parse", "HEAD")
        self.git("remote", "add", "origin", origin)
        self.route(f"repos/{REPO}", {"default_branch": "main"})
        self.route(f"repos/{REPO}/actions/workflows?per_page=1", {"total_count": 3})

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()

    # ---- beads

    def bead(self, bead_id: str, metadata: dict) -> None:
        (self.beads / f"{bead_id}.json").write_text(json.dumps([{"id": bead_id, "metadata": metadata}]), encoding="utf-8")

    def implementation_item(self, *, push: str = "true", open_pr: str = "true", root_extra: dict | None = None) -> None:
        """A do-work item: step -> item root -> source anchor with the worktree."""
        self.bead("step", {"gc.root_bead_id": "root"})
        self.bead("root", {"gc.var.push": push, "gc.var.open_pr": open_pr, "gc.drain_member_id": "anchor", **(root_extra or {})})
        self.bead("anchor", {"work_dir": str(self.repo)})

    def fix_loop(self, *, push: str = "true", open_pr: str = "true", commit: str = "", branch: str = "") -> None:
        """A fix loop: a standalone root with no source anchor; the worker records the handoff."""
        metadata = {"gc.var.push": push, "gc.var.open_pr": open_pr}
        if commit:
            metadata["gc.build.handoff_commit"] = commit
        if branch:
            metadata["gc.build.handoff_branch"] = branch
        self.bead("step", {"gc.root_bead_id": "root"})
        self.bead("root", metadata)

    # ---- GitHub

    def route(self, path: str, payload) -> None:
        (self.gh_dir / f"{route(path)}.json").write_text(json.dumps(payload), encoding="utf-8")

    def route_error(self, path: str, message: str) -> None:
        (self.gh_dir / f"{route(path)}.error").write_text(message + "\n", encoding="utf-8")

    def pulls(self, pulls: list[dict], *, branch: str = BRANCH, state: str = "open") -> None:
        self.route(f"repos/{REPO}/pulls?head=acme:{branch}&state={state}&per_page=100", pulls)

    def pull(self, *, number: int = 7, head: str | None = None, state: str = "open", draft: bool = True) -> dict:
        return {
            "number": number,
            "state": state,
            "draft": draft,
            "html_url": f"https://github.com/{REPO}/pull/{number}",
            "head": {"sha": head or self.head, "ref": BRANCH},
        }

    def checks(self, runs: list[dict], *, sha: str | None = None, statuses: list[dict] | None = None) -> None:
        sha = sha or self.head
        self.route(
            f"repos/{REPO}/commits/{sha}/check-runs?filter=latest&per_page=100&page=1",
            {"total_count": len(runs), "check_runs": runs},
        )
        self.route(f"repos/{REPO}/commits/{sha}/status?per_page=100", {"state": "pending", "statuses": statuses or []})

    def gh_calls(self) -> list[str]:
        log = self.gh_dir / "calls.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    # ---- run

    def run(self, *args: str, script: pathlib.Path = SCRIPT, env: dict | None = None, path: str | None = None, bead: str | None = "step") -> subprocess.CompletedProcess:
        full_env = {
            "PATH": path if path is not None else f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": os.environ.get("HOME", str(self.city)),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "GH_STUB_DIR": str(self.gh_dir),
            "BD_SHOW_DIR": str(self.beads),
            "GC_STORE_PATH": str(self.repo),
            "PR_CI_GREEN_RETRY_SLEEP_SECONDS": "0",
            **(env or {}),
        }
        if bead is not None:
            full_env["GC_BEAD_ID"] = bead
        return subprocess.run([str(script), *args], env=full_env, cwd=str(self.city), capture_output=True, text=True, check=False)


class PrCiGreenTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = Fixture(pathlib.Path(self._tmp.name).resolve())

    def assert_pass(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pr-ci-green: PASS", result.stdout)

    def assert_skipped(self, result: subprocess.CompletedProcess, reason: str) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pr-ci-green: skipped: ", result.stdout)
        self.assertIn(reason, result.stdout)
        self.assertNotIn("PASS", result.stdout)

    def assert_fail(self, result: subprocess.CompletedProcess, *fragments: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("pr-ci-green: FAIL", result.stderr)
        self.assertNotIn("PASS", result.stdout)
        self.assertNotIn("skipped", result.stdout)
        for fragment in fragments:
            self.assertIn(fragment, result.stderr)


class ImplementationHandoffTests(PrCiGreenTestCase):
    """The gate on the step that ends implementation (worktree head is the handoff)."""

    def setUp(self) -> None:
        super().setUp()
        self.fx.implementation_item()
        self.fx.pulls([self.fx.pull()])

    def test_green_checks_on_the_pull_request_head_pass(self) -> None:
        self.fx.checks(
            [check_run("ci-gate"), check_run("build (24.x)"), check_run("deploy", conclusion="skipped"), check_run("lint", conclusion="neutral")],
            statuses=[{"context": "license/cla", "state": "success", "target_url": "https://example.test/cla"}],
        )

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn(f"{REPO}#7 head={self.fx.head} checks=5 (passed 4, skipped 1)", result.stdout)
        self.assertIn("pr_state=draft", result.stdout)

    def test_a_failed_check_fails_and_is_listed_with_its_url(self) -> None:
        red = check_run("build (24.x)", conclusion="failure")
        self.fx.checks([check_run("ci-gate"), red])

        result = self.fx.run()

        self.assert_fail(result, "1 failed, 0 unfinished, of 2 checks", f"FAILED  build (24.x) (failure) {red['html_url']}")

    def test_infrastructure_conclusions_are_red_not_classified(self) -> None:
        for conclusion in ("cancelled", "timed_out", "startup_failure", "action_required", "stale"):
            with self.subTest(conclusion=conclusion):
                self.fx.checks([check_run("ci-gate"), check_run("runner", conclusion=conclusion)])
                self.assert_fail(self.fx.run(), f"FAILED  runner ({conclusion})")

    def test_an_unfinished_check_fails_and_is_listed_with_its_url(self) -> None:
        running = check_run("vitest browser (shard 3/4)", status="in_progress", conclusion=None)
        queued = check_run("playwright", status="queued", conclusion=None)
        self.fx.checks([check_run("ci-gate"), running, queued])

        result = self.fx.run()

        self.assert_fail(
            result,
            "0 failed, 2 unfinished, of 3 checks",
            f"PENDING vitest browser (shard 3/4) (in_progress) {running['html_url']}",
            f"PENDING playwright (queued) {queued['html_url']}",
            "Wait for the unfinished checks to complete",
        )

    def test_only_skipped_checks_is_green(self) -> None:
        self.fx.checks([check_run("deploy", conclusion="skipped"), check_run("docs", conclusion="skipped")])

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn("checks=2 (passed 0, skipped 2)", result.stdout)

    def test_a_failed_commit_status_is_red(self) -> None:
        self.fx.checks(
            [check_run("ci-gate")],
            statuses=[{"context": "coverage/project", "state": "failure", "target_url": "https://example.test/cov"}],
        )

        self.assert_fail(self.fx.run(), "FAILED  coverage/project (failure) https://example.test/cov")

    def test_no_pull_request_fails_when_the_workflow_publishes(self) -> None:
        self.fx.pulls([])
        self.fx.route(f"repos/{REPO}/compare/main...{self.fx.head}", {"ahead_by": 1, "status": "ahead"})

        result = self.fx.run()

        self.assert_fail(result, f"no open pull request for branch {BRANCH} in {REPO}", "gh pr create --draft")

    def test_unpushed_commit_fails_when_the_workflow_publishes(self) -> None:
        self.fx.pulls([])  # the compare call 404s: GitHub has never seen the commit

        self.assert_fail(self.fx.run(), f"no open pull request for branch {BRANCH}")

    def test_a_step_that_changed_no_code_is_skipped_not_forced_to_open_a_pull_request(self) -> None:
        self.fx.git("checkout", "-q", "--detach", self.fx.base)
        self.fx.route(f"repos/{REPO}/commits/{self.fx.base}/pulls?per_page=100", [self.fx.pull(number=3, head=self.fx.base, state="closed")])
        self.fx.route(f"repos/{REPO}/compare/main...{self.fx.base}", {"ahead_by": 0, "status": "identical"})

        self.assert_skipped(self.fx.run(), "nothing to publish")

    def test_pull_request_head_that_is_not_the_handoff_commit_fails(self) -> None:
        stale = "1111111111111111111111111111111111111111"
        self.fx.pulls([self.fx.pull(head=stale)])
        self.fx.checks([check_run("ci-gate")], sha=stale)  # green, but on the wrong commit

        result = self.fx.run()

        self.assert_fail(result, "head mismatch", f"{REPO}#7 head is {stale[:12]}", f"handed to review is {self.fx.head[:12]}")

    def test_recorded_handoff_that_is_not_the_worktree_head_fails(self) -> None:
        self.fx.implementation_item(root_extra={"gc.build.handoff_commit": self.fx.base})

        result = self.fx.run()

        self.assert_fail(result, "gc.build.handoff_commit on root", "implementation worktree")
        self.assertEqual(self.fx.gh_calls(), [])

    def test_no_check_reported_yet_is_not_green(self) -> None:
        self.fx.checks([])

        self.assert_fail(self.fx.run(), f"no check has reported on {self.fx.head[:12]}")

    def test_repository_without_ci_is_an_explicit_skip(self) -> None:
        self.fx.checks([])
        self.fx.route(f"repos/{REPO}/actions/workflows?per_page=1", {"total_count": 0})

        self.assert_skipped(self.fx.run(), f"{REPO} has no workflows")

    def test_more_than_one_open_pull_request_for_the_branch_fails(self) -> None:
        other = "2222222222222222222222222222222222222222"
        self.fx.pulls([self.fx.pull(number=7, head=other), self.fx.pull(number=9, head=other)])

        self.assert_fail(self.fx.run(), "more than one pull request", "#7", "#9")

    def test_github_being_unreadable_is_a_failure_not_a_skip(self) -> None:
        self.fx.route_error(
            f"repos/{REPO}/commits/{self.fx.head}/check-runs?filter=latest&per_page=100&page=1",
            "gh: Server Error (HTTP 502)",
        )

        self.assert_fail(self.fx.run(), "GitHub could not be read", "HTTP 502", "the gate does not pass")

    def test_long_failure_lists_are_capped_for_the_attempt_log(self) -> None:
        self.fx.checks([check_run(f"job-{index:02d}", conclusion="failure") for index in range(30)])

        result = self.fx.run()

        self.assert_fail(result, "30 failed", "FAILED  job-24", "... and 5 more")
        self.assertNotIn("job-25", result.stderr)
        self.assertLess(len(result.stderr.encode("utf-8")), 4096)

    def test_closed_source_anchor_hands_over_its_recorded_commit(self) -> None:
        self.fx.bead("anchor", {"work_dir": str(self.fx.tmp / "reaped"), "gc.work_commit": self.fx.head, "gc.work_branch": BRANCH})
        self.fx.checks([check_run("ci-gate")])

        self.assert_pass(self.fx.run())


class PublishingIntentTests(PrCiGreenTestCase):
    def test_no_publishing_intent_and_no_handoff_skips_without_asking_github(self) -> None:
        for push, open_pr in (("false", "false"), ("true", "false"), ("false", "true"), ("", "")):
            with self.subTest(push=push, open_pr=open_pr):
                self.fx.fix_loop(push=push, open_pr=open_pr)

                result = self.fx.run()

                self.assert_skipped(result, "no publishing intent")
                self.assertIn(f"push={push or 'unset'} open_pr={open_pr or 'unset'}", result.stdout)
                self.assertEqual(self.fx.gh_calls(), [])

    def test_no_publishing_intent_and_no_pull_request_skips(self) -> None:
        self.fx.implementation_item(push="false", open_pr="false")
        self.fx.pulls([])

        self.assert_skipped(self.fx.run(), f"no open pull request for branch {BRANCH}")

    def test_an_existing_pull_request_is_checked_even_without_publishing_intent(self) -> None:
        self.fx.implementation_item(push="false", open_pr="false")
        self.fx.pulls([self.fx.pull(draft=False)])
        self.fx.checks([check_run("ci-gate", conclusion="failure")])

        self.assert_fail(self.fx.run(), "FAILED  ci-gate (failure)")

    def test_publishing_intent_without_a_recorded_handoff_fails(self) -> None:
        self.fx.fix_loop()

        self.assert_fail(self.fx.run(), "intends to publish (push=true open_pr=true)", "gc.build.handoff_commit")


class FixLoopHandoffTests(PrCiGreenTestCase):
    """The gate on apply-fixes: the worker records the commit it hands to re-review."""

    def test_recorded_commit_at_the_pull_request_head_with_green_checks_passes(self) -> None:
        self.fx.fix_loop(commit=self.fx.head, branch=BRANCH)
        self.fx.pulls([self.fx.pull(draft=False)])
        self.fx.checks([check_run("ci-gate")])

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn("pr_state=open", result.stdout)

    def test_recorded_commit_behind_the_pull_request_head_fails(self) -> None:
        newer = "3333333333333333333333333333333333333333"
        self.fx.fix_loop(commit=self.fx.head, branch=BRANCH)
        self.fx.pulls([self.fx.pull(head=newer)])

        self.assert_fail(self.fx.run(), "head mismatch", newer[:12], self.fx.head[:12])

    def test_recorded_commit_without_a_branch_finds_the_pull_request_by_commit(self) -> None:
        self.fx.fix_loop(commit=self.fx.head)
        self.fx.route(
            f"repos/{REPO}/commits/{self.fx.head}/pulls?per_page=100",
            [self.fx.pull(number=2, head=self.fx.head, state="closed"), self.fx.pull(number=7)],
        )
        self.fx.checks([check_run("ci-gate", status="in_progress", conclusion=None)])

        self.assert_fail(self.fx.run(), f"{REPO}#7 head {self.fx.head[:12]}", "PENDING ci-gate")

    def test_recorded_value_that_is_not_a_sha_fails(self) -> None:
        self.fx.fix_loop(commit="HEAD", branch=BRANCH)

        self.assert_fail(self.fx.run(), "is not a commit sha")


class EnvironmentTests(PrCiGreenTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.fx.implementation_item()
        self.fx.pulls([self.fx.pull()])
        self.fx.checks([check_run("ci-gate", conclusion="failure")])

    def test_remote_that_is_not_github_is_an_explicit_skip(self) -> None:
        self.fx.git("remote", "set-url", "origin", "https://oauth2:secret@gitlab.example.com/acme/widgets.git")

        result = self.fx.run()

        self.assert_skipped(result, "origin is not a github.com remote")
        self.assertNotIn("secret", result.stdout)
        self.assertEqual(self.fx.gh_calls(), [])

    def test_repository_without_a_remote_is_an_explicit_skip(self) -> None:
        self.fx.git("remote", "remove", "origin")

        self.assert_skipped(self.fx.run(), "no git origin remote")

    def test_missing_gh_is_an_explicit_skip(self) -> None:
        (self.fx.bin / "gh").unlink()

        result = self.fx.run(path=f"{self.fx.bin}:/usr/bin:/bin", env={"PR_CI_GREEN_GH_FALLBACK_PATHS": ""})

        self.assert_skipped(result, "gh is not installed")

    def test_signed_out_gh_is_an_explicit_skip(self) -> None:
        (self.fx.gh_dir / "signed-out").write_text("", encoding="utf-8")

        self.assert_skipped(self.fx.run(), "gh is not signed in")

    def test_controller_sandbox_home_falls_back_to_the_account_home_for_gh(self) -> None:
        # The controller runs checks with HOME set to the city, where gh has no
        # login. The gate must still reach a verdict there, not skip.
        real_home = pwd.getpwuid(os.getuid()).pw_dir
        (self.fx.gh_dir / "signed-in-home").write_text(real_home, encoding="utf-8")

        result = self.fx.run(env={"HOME": str(self.fx.city)})

        self.assert_fail(result, "FAILED  ci-gate (failure)")

    def test_ssh_remote_urls_resolve_the_repository(self) -> None:
        for url in (f"git@github.com:{REPO}.git", f"ssh://git@github.com/{REPO}", f"https://github.com/{REPO}"):
            with self.subTest(url=url):
                self.fx.git("remote", "set-url", "origin", url)
                self.assert_fail(self.fx.run(), f"{REPO}#7")

    def test_gate_mode_requires_a_bead(self) -> None:
        self.assert_fail(self.fx.run(bead=None), "GC_BEAD_ID is required")


class ManualModeTests(PrCiGreenTestCase):
    def test_pull_request_number_checks_its_head(self) -> None:
        self.fx.route(f"repos/{REPO}/pulls/7", self.fx.pull())
        self.fx.checks([check_run("ci-gate")])

        self.assert_pass(self.fx.run("--repo", REPO, "--pr", "7", bead=None))

    def test_merged_pull_request_needs_any_state(self) -> None:
        self.fx.route(f"repos/{REPO}/pulls/7", self.fx.pull(state="closed", draft=False))
        self.fx.checks([check_run("ci-gate")])

        self.assert_fail(self.fx.run("--repo", REPO, "--pr", "7", bead=None), "is closed, not open")
        self.assert_pass(self.fx.run("--repo", REPO, "--pr", "7", "--any-state", bead=None))

    def test_any_state_inspects_an_older_head_of_the_pull_request(self) -> None:
        older = "4444444444444444444444444444444444444444"
        self.fx.route(f"repos/{REPO}/pulls/7", self.fx.pull(state="closed", draft=False))
        self.fx.checks([check_run("build (24.x)", conclusion="failure")], sha=older)

        result = self.fx.run("--repo", REPO, "--pr", "7", "--commit", older, "--any-state", bead=None)

        self.assert_fail(result, f"head {older[:12]}: 1 failed", "FAILED  build (24.x) (failure)")


class ImplementationHandoffChainTests(PrCiGreenTestCase):
    """implementation-handoff-valid.sh: artifact gate first, then the CI gate."""

    SUMMARY = (
        "---\n"
        "schema: gc.build.implementation-summary.v1\n"
        "workflow:\n  id: root\n  formula: do-work\n"
        "methodology:\n  pack: gascity\n  name: build-basic\n"
        "producer:\n  formula: do-work\n  stage: implement\n  attempt: 1\n"
        "status: approved\n"
        "trace:\n"
        "  upstream:\n    - path: beads/anchor\n      hash: bead:anchor\n"
        "  coverage:\n    - id: AC-1\n      status: covered\n"
        "---\n\n"
        "## Summary\n\n| ID | Status |\n| --- | --- |\n| AC-1 | covered |\n\n"
        "## Intended Behavior\n\nx\n\n## Changed Files\n\nx\n\n## Verification\n\nx\n\n## Remaining Risks\n\nx\n"
    )

    def setUp(self) -> None:
        super().setUp()
        self.summary = self.fx.tmp / "task-anchor-summary.md"
        self.summary.write_text(self.SUMMARY, encoding="utf-8")
        self.item()
        self.fx.pulls([self.fx.pull()])

    def item(self, **intent: str) -> None:
        self.fx.implementation_item(root_extra={"gc.implementation.summary_path": str(self.summary)}, **intent)
        self.fx.bead(
            "step",
            {
                "gc.root_bead_id": "root",
                "gc.build.artifact_schema": "gc.build.implementation-summary.v1",
                "gc.build.artifact_path_keys": "gc.implementation.summary_path",
            },
        )

    def test_valid_summary_and_green_checks_pass_both_gates(self) -> None:
        self.fx.checks([check_run("ci-gate")])

        result = self.fx.run(script=HANDOFF_SCRIPT)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("build artifact valid", result.stdout)
        self.assertIn("pr-ci-green: PASS", result.stdout)

    def test_valid_summary_with_red_checks_fails(self) -> None:
        self.fx.checks([check_run("ci-gate", conclusion="failure")])

        result = self.fx.run(script=HANDOFF_SCRIPT)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("build artifact valid", result.stdout)
        self.assertIn("FAILED  ci-gate (failure)", result.stderr)

    def test_invalid_summary_fails_before_github_is_asked(self) -> None:
        self.summary.write_text(self.SUMMARY.replace("status: approved", "status: bogus"), encoding="utf-8")
        self.fx.checks([check_run("ci-gate")])

        result = self.fx.run(script=HANDOFF_SCRIPT)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("build-artifact-check:", result.stderr)
        self.assertEqual(self.fx.gh_calls(), [])

    def test_workflow_that_does_not_publish_passes_on_the_artifact_alone(self) -> None:
        self.item(push="false", open_pr="false")
        self.fx.pulls([])

        result = self.fx.run(script=HANDOFF_SCRIPT)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pr-ci-green: skipped: no publishing intent", result.stdout)


if __name__ == "__main__":
    unittest.main()
