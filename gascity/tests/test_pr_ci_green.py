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
import time
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
# <key>.flaky holds how many more times the route answers with <key>.error
# before it serves <key>.json.
if [ -f "$dir/$key.flaky" ] && [ "$(cat "$dir/$key.flaky")" -gt 0 ]; then
  echo $(( $(cat "$dir/$key.flaky") - 1 )) >"$dir/$key.flaky"
  cat "$dir/$key.error" >&2; exit 1
fi
if [ -f "$dir/$key.json" ]; then cat "$dir/$key.json"; exit 0; fi
if [ -f "$dir/$key.error" ]; then cat "$dir/$key.error" >&2; exit 1; fi
# A route no test declared is a bug in the test or an API call nobody meant to
# make. It is never a quiet 404.
echo "$2" >>"$dir/unrouted.log"
echo "stub gh: UNROUTED $2" >&2
exit 97
"""

GC_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
dir="${BD_SHOW_DIR:?}"
if [ "${1:-}" = "bd" ] && [ "${2:-}" = "show" ]; then
  id="$3"
  if [ -f "$dir/$id.flaky" ] && [ "$(cat "$dir/$id.flaky")" -gt 0 ]; then
    echo $(( $(cat "$dir/$id.flaky") - 1 )) >"$dir/$id.flaky"
    cat "$dir/$id.error" >&2; exit 1
  fi
  if [ -f "$dir/$id.json" ]; then cat "$dir/$id.json"; exit 0; fi
  if [ -f "$dir/$id.error" ]; then cat "$dir/$id.error" >&2; exit 1; fi
  echo "Error fetching $id: no issue found matching \"$id\"" >&2
  exit 1
fi
if [ "${1:-}" = "bd" ] && [ "${2:-}" = "update" ]; then
  if [ -f "$dir/update-fails" ]; then echo "events: lock timed out" >&2; exit 1; fi
  shift 2
  echo "$*" >>"$dir/updates.log"
  exit 0
fi
echo "stub gc: unsupported: $*" >&2
exit 1
"""


OLDER_1 = "a" * 40
OLDER_2 = "b" * 40
NOT_FOUND = "gh: Not Found (HTTP 404)"
RATE_LIMITED = "gh: API rate limit exceeded for user ID 1. (HTTP 403)"
NO_VERDICT = 75


def route(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", path)


def job_id(name: str, run: int) -> int:
    return run * 1_000_000 + sum(ord(char) * (index + 1) for index, char in enumerate(name)) % 900_000


def check_run(
    name: str,
    status: str = "completed",
    conclusion: str | None = "success",
    *,
    run: int = 1,
    started: str = "2026-10-01T10:00:00Z",
    app: str = "github-actions",
) -> dict:
    """A check run as GitHub lists it. `run` is the workflow run it belongs to
    (run 1 is workflow "CI" on the pull request side unless a test says otherwise)."""
    number = job_id(name, run)
    url = (
        f"https://github.com/{REPO}/actions/runs/{run}/job/{number}"
        if app == "github-actions"
        else f"https://{app}.example.test/{name}"
    )
    return {
        "id": number,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started,
        "html_url": url,
        "app": {"slug": app},
    }


def base_run(name: str, conclusion: str = "success", status: str = "completed", *, run: int = 9) -> dict:
    """A check run on the base branch (run 9 is workflow "CI" there unless a test says otherwise)."""
    return check_run(name, status, conclusion if status == "completed" else None, run=run)


def workflow_run(run: int, name: str = "CI", status: str = "completed", started: str = "2026-10-01T10:00:00Z") -> dict:
    return {
        "id": run,
        "name": name,
        "status": status,
        "conclusion": "success" if status == "completed" else None,
        "run_started_at": started,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run}",
    }


class Fixture:
    """One temp dir holding a git repo, bead JSON, gh routes and the stub binaries.

    The base branch is `main`: protected, requiring `ci-gate`, with a green
    `ci-gate` on its head and no older commits, unless a test says otherwise.
    """

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
        self.base_branch(required=["ci-gate"])
        self.base_checks([base_run("ci-gate")])
        self.base_history([])

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

    def recorded_results(self) -> list[str]:
        log = self.beads / "updates.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    # ---- GitHub

    def route(self, path: str, payload) -> None:
        (self.gh_dir / f"{route(path)}.json").write_text(json.dumps(payload), encoding="utf-8")

    def route_error(self, path: str, message: str, *, times: int | None = None) -> None:
        """Answer path with an error: always, or `times` times before its JSON."""
        (self.gh_dir / f"{route(path)}.error").write_text(message + "\n", encoding="utf-8")
        if times is None:
            (self.gh_dir / f"{route(path)}.json").unlink(missing_ok=True)
        else:
            (self.gh_dir / f"{route(path)}.flaky").write_text(str(times), encoding="utf-8")

    def check_runs_path(self, sha: str | None = None) -> str:
        return f"repos/{REPO}/commits/{sha or self.head}/check-runs?filter=latest&per_page=100&page=1"

    def pulls(self, pulls: list[dict], *, branch: str = BRANCH, state: str = "open") -> None:
        self.route(f"repos/{REPO}/pulls?head=acme:{branch}&state={state}&per_page=100", pulls)
        self.pull_details(pulls)

    def commit_pulls(self, pulls: list[dict] | None, *, sha: str | None = None) -> None:
        """Pull requests GitHub associates with a commit; None means it was never pushed."""
        path = f"repos/{REPO}/commits/{sha or self.head}/pulls?per_page=100"
        if pulls is None:
            self.route_error(path, NOT_FOUND)
        else:
            self.route(path, pulls)
            self.pull_details(pulls)

    def pull_details(self, pulls: list[dict], *, mergeable: bool | None = True, mergeable_state: str = "clean") -> None:
        """The single-pull-request read, which is the one that says whether it can be merged."""
        for pull in pulls:
            self.route(
                f"repos/{REPO}/pulls/{pull['number']}", {**pull, "mergeable": mergeable, "mergeable_state": mergeable_state}
            )

    def pull(self, *, number: int = 7, head: str | None = None, state: str = "open", draft: bool = True, ref: str = BRANCH) -> dict:
        return {
            "number": number,
            "state": state,
            "draft": draft,
            "html_url": f"https://github.com/{REPO}/pull/{number}",
            "head": {"sha": head or self.head, "ref": ref},
            "base": {"ref": "main"},
        }

    def checks(
        self,
        runs: list[dict],
        *,
        sha: str | None = None,
        statuses: list[dict] | None = None,
        workflow_runs: list[dict] | None = None,
    ) -> None:
        sha = sha or self.head
        workflow_runs = [workflow_run(1)] if workflow_runs is None else workflow_runs
        self.route(self.check_runs_path(sha), {"total_count": len(runs), "check_runs": runs})
        self.route(f"repos/{REPO}/commits/{sha}/status?per_page=100", {"state": "pending", "statuses": statuses or []})
        self.route(
            f"repos/{REPO}/actions/runs?head_sha={sha}&per_page=100",
            {"total_count": len(workflow_runs), "workflow_runs": workflow_runs},
        )

    def base_branch(self, *, required: list[str] | None, protected: bool = True, rulesets: list[dict] | None = None) -> None:
        """`required=None` on a protected branch: the protection is not shown to this login."""
        branch: dict = {"name": "main", "commit": {"sha": self.base}, "protected": protected}
        if protected and required is not None:
            branch["protection"] = {
                "enabled": True,
                "required_status_checks": {
                    "enforcement_level": "everyone",
                    "contexts": required,
                    "checks": [{"context": name, "app_id": 15368} for name in required],
                },
            }
        self.route(f"repos/{REPO}/branches/main", branch)
        self.route(f"repos/{REPO}/rules/branches/main?per_page=100", rulesets or [])

    def base_checks(
        self,
        runs: list[dict],
        *,
        sha: str | None = None,
        statuses: list[dict] | None = None,
        workflow_runs: list[dict] | None = None,
    ) -> None:
        sha = sha or self.base
        workflow_runs = [workflow_run(9)] if workflow_runs is None else workflow_runs
        self.route(self.check_runs_path(sha), {"total_count": len(runs), "check_runs": runs})
        self.route(f"repos/{REPO}/commits/{sha}/status?per_page=100", {"state": "success", "statuses": statuses or []})
        self.route(
            f"repos/{REPO}/actions/runs?head_sha={sha}&per_page=100",
            {"total_count": len(workflow_runs), "workflow_runs": workflow_runs},
        )

    def job(self, run: dict, *, workflow: str = "CI", failed: tuple[str, ...] = ()) -> None:
        """The Actions job behind a check run: its workflow and which steps failed."""
        steps = [{"name": "Set up job", "status": "completed", "conclusion": "success", "number": 1}]
        steps += [
            {"name": name, "status": "completed", "conclusion": "failure", "number": index + 2}
            for index, name in enumerate(failed)
        ]
        steps.append({"name": "Post checkout", "status": "completed", "conclusion": "skipped", "number": len(steps) + 1})
        self.route(
            f"repos/{REPO}/actions/jobs/{run['id']}",
            {"id": run["id"], "name": run["name"], "workflow_name": workflow, "conclusion": run["conclusion"], "steps": steps},
        )

    def base_history(self, older: list[str]) -> None:
        """Commits on main behind its head, newest first."""
        self.route(f"repos/{REPO}/commits?sha=main&per_page=11", [{"sha": sha} for sha in [self.base, *older]])

    def gh_calls(self) -> list[str]:
        log = self.gh_dir / "calls.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def unrouted(self) -> list[str]:
        log = self.gh_dir / "unrouted.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    # ---- run

    def run(
        self,
        *args: str,
        script: pathlib.Path = SCRIPT,
        env: dict | None = None,
        path: str | None = None,
        bead: str | None = "step",
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        full_env = {
            "PATH": path if path is not None else f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": os.environ.get("HOME", str(self.city)),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "GH_STUB_DIR": str(self.gh_dir),
            "BD_SHOW_DIR": str(self.beads),
            "GC_STORE_PATH": str(self.repo),
            "PR_CI_GREEN_RETRY_SLEEP_SECONDS": "0.05",
            # Long enough that a retry always happens on a loaded host; a test
            # of a failure that never clears sets its own short budget.
            "PR_CI_GREEN_INFRA_BUDGET_SECONDS": "30",
            **(env or {}),
        }
        if bead is not None:
            full_env["GC_BEAD_ID"] = bead
        return subprocess.run(
            [str(script), *args], env=full_env, cwd=str(self.city), capture_output=True, text=True, check=False, timeout=timeout
        )


class PrCiGreenTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = Fixture(pathlib.Path(self._tmp.name).resolve())
        self.addCleanup(self.assert_every_github_call_was_declared)

    def assert_every_github_call_was_declared(self) -> None:
        self.assertEqual(self.fx.unrouted(), [], "the gate called GitHub routes this test did not declare")

    def assert_pass(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pr-ci-green: PASS", result.stdout)

    def assert_skipped(self, result: subprocess.CompletedProcess, reason: str, *, loud: bool | None = None) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pr-ci-green: skipped: ", result.stdout)
        self.assertIn(reason, result.stdout)
        self.assertNotIn("PASS", result.stdout)
        # A skip is never only a stdout line.
        self.assertIn(reason, result.stderr)
        if loud is True:
            self.assertIn("pr-ci-green: WARNING the CI gate did not run and the step passes unchecked", result.stderr)
        elif loud is False:
            self.assertIn("pr-ci-green: skipped: ", result.stderr)
            self.assertNotIn("WARNING", result.stderr)

    def assert_fail(self, result: subprocess.CompletedProcess, *fragments: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("pr-ci-green: FAIL", result.stderr)
        self.assertNotIn("PASS", result.stdout)
        self.assertNotIn("skipped", result.stdout)
        for fragment in fragments:
            self.assertIn(fragment, result.stderr)

    def assert_no_verdict(self, result: subprocess.CompletedProcess, *fragments: str) -> None:
        self.assertEqual(result.returncode, NO_VERDICT, result.stdout + result.stderr)
        self.assertIn("pr-ci-green: INFRA", result.stderr)
        self.assertNotIn("pr-ci-green: FAIL", result.stderr)
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
        self.assertNotIn("WARNING", result.stdout + result.stderr)
        # The result is kept on the workflow root.
        self.assertEqual(len(self.fx.recorded_results()), 1)
        self.assertIn("root --set-metadata gc.build.ci_gate_result=PASS", self.fx.recorded_results()[0])

    def test_a_result_that_cannot_be_recorded_still_passes(self) -> None:
        self.fx.checks([check_run("ci-gate")])
        (self.fx.beads / "update-fails").write_text("", encoding="utf-8")

        self.assert_pass(self.fx.run())

    def test_a_pull_request_marked_ready_fails_with_the_command_that_makes_it_a_draft_again(self) -> None:
        # A ready, green, unreviewed pull request is what a merge sweep merges.
        self.fx.pulls([self.fx.pull(draft=False)])
        self.fx.checks([check_run("ci-gate")])

        result = self.fx.run()

        self.assert_fail(
            result,
            f"{REPO}#7 is marked ready for review but has not been reviewed",
            f"gh pr ready --undo 7 --repo {REPO}",
            "Only the publish step marks it ready",
        )

    def test_a_failed_check_fails_and_is_listed_with_its_url(self) -> None:
        red = check_run("build (24.x)", conclusion="failure")
        self.fx.checks([check_run("ci-gate"), red])

        result = self.fx.run()

        self.assert_fail(
            result,
            "1 failed, 0 unfinished, of 2 checks",
            f"FAILED  build (24.x) (failure) {red['html_url']} [main: no result]",
            "fix what this branch broke",
            "gc.outcome=fail and gc.failure_class=hard",
        )

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
            "Wait for the unfinished checks and workflow runs to complete",
        )

    def test_a_workflow_run_still_in_progress_fails_even_when_every_check_run_is_green(self) -> None:
        # A job behind `needs:` has no check run until it is queued, so the
        # check runs that exist can all be green while CI is still running.
        run_url = f"https://github.com/{REPO}/actions/runs/55"
        for status in ("queued", "in_progress", "waiting", "requested", "pending"):
            with self.subTest(status=status):
                self.fx.checks(
                    [check_run("ci-gate"), check_run("changes")],
                    workflow_runs=[
                        {"name": "Run Django Tests", "status": "completed", "conclusion": "success", "html_url": "https://example.test/1"},
                        {"name": "Build SPA Frontend", "status": status, "conclusion": None, "html_url": run_url},
                    ],
                )

                result = self.fx.run()

                self.assert_fail(
                    result,
                    "0 failed, 1 unfinished, of 2 checks",
                    f"PENDING workflow run: Build SPA Frontend ({status}) {run_url}",
                    "Wait for the unfinished checks and workflow runs to complete",
                )

    def test_a_required_check_that_has_not_reported_fails(self) -> None:
        self.fx.base_branch(required=["ci-gate", "vitest-browser-gate"])
        self.fx.checks([check_run("ci-gate"), check_run("changes")])

        result = self.fx.run()

        self.assert_fail(
            result,
            "0 failed, 1 unfinished, of 2 checks",
            "MISSING vitest-browser-gate (required by main, has not reported on this commit)",
        )

    def test_a_check_required_by_a_ruleset_must_report_too(self) -> None:
        self.fx.base_branch(
            required=[],
            protected=False,
            rulesets=[
                {"type": "pull_request"},
                {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "ruleset-gate"}]}},
            ],
        )
        self.fx.checks([check_run("ci-gate")])

        self.assert_fail(self.fx.run(), "MISSING ruleset-gate (required by main")

    def test_only_skipped_checks_is_green(self) -> None:
        self.fx.base_branch(required=[])
        self.fx.checks([check_run("deploy", conclusion="skipped"), check_run("docs", conclusion="skipped")])

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn("checks=2 (passed 0, skipped 2)", result.stdout)

    def test_a_failed_commit_status_the_base_branch_requires_is_red(self) -> None:
        self.fx.base_branch(required=["ci-gate", "coverage/project"])
        self.fx.checks(
            [check_run("ci-gate")],
            statuses=[{"context": "coverage/project", "state": "failure", "target_url": "https://example.test/cov"}],
        )

        self.assert_fail(self.fx.run(), "FAILED  coverage/project (failure) https://example.test/cov [required by main]")

    def test_no_pull_request_fails_when_the_workflow_publishes(self) -> None:
        self.fx.pulls([])
        self.fx.commit_pulls([])
        self.fx.route(f"repos/{REPO}/compare/main...{self.fx.head}", {"ahead_by": 1, "status": "ahead"})

        result = self.fx.run()

        self.assert_fail(
            result, f"no open pull request for branch {BRANCH} or commit {self.fx.head[:12]} in {REPO}", "gh pr create --draft"
        )

    def test_unpushed_commit_fails_when_the_workflow_publishes(self) -> None:
        self.fx.pulls([])
        self.fx.commit_pulls(None)  # GitHub has never seen the commit
        self.fx.route_error(f"repos/{REPO}/compare/main...{self.fx.head}", NOT_FOUND)

        self.assert_fail(self.fx.run(), f"no open pull request for branch {BRANCH}")

    def test_a_branch_pushed_under_another_name_is_found_by_its_commit(self) -> None:
        # The worktree branch is BRANCH; it was pushed as another ref, so no
        # pull request has BRANCH as its head.
        self.fx.pulls([])
        self.fx.commit_pulls([self.fx.pull(number=12, ref="gcas-abc123-renamed-on-push")])
        self.fx.checks([check_run("ci-gate")])

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn(f"{REPO}#12 head={self.fx.head}", result.stdout)

    def test_a_step_that_changed_no_code_is_skipped_not_forced_to_open_a_pull_request(self) -> None:
        self.fx.git("checkout", "-q", "--detach", self.fx.base)
        self.fx.commit_pulls([self.fx.pull(number=3, head=self.fx.base, state="closed")], sha=self.fx.base)
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

        self.assert_skipped(self.fx.run(), f"{REPO} has no workflows", loud=True)

    def test_more_than_one_open_pull_request_for_the_branch_fails(self) -> None:
        other = "2222222222222222222222222222222222222222"
        self.fx.pulls([self.fx.pull(number=7, head=other), self.fx.pull(number=9, head=other)])

        self.assert_fail(self.fx.run(), "more than one pull request", "#7", "#9")

    def test_long_failure_lists_are_capped_for_the_attempt_log(self) -> None:
        self.fx.checks([check_run("ci-gate")] + [check_run(f"job-{index:02d}", conclusion="failure") for index in range(30)])

        result = self.fx.run()

        self.assert_fail(result, "30 failed, 0 unfinished, of 31 checks", "FAILED  job-24", "... and 5 more", "gc.failure_class=hard")
        self.assertNotIn("job-25", result.stderr)
        self.assertLess(len(result.stderr.encode("utf-8")), 4096)

    def test_long_check_names_and_urls_still_leave_room_for_the_advice(self) -> None:
        runs = []
        for index in range(30):
            run = check_run(f"visual-regression shard {index:02d} " + "x" * 60, conclusion="failure")
            run["html_url"] = f"https://github.com/{REPO}/actions/runs/36886252649/job/1104507169{index:02d}"
            runs.append(run)
        self.fx.checks([check_run("ci-gate"), *runs])

        result = self.fx.run()

        self.assert_fail(result, "30 failed", "more", "gc.failure_class=hard")
        self.assertLess(len(result.stderr.encode("utf-8")), 4096)

    def test_closed_source_anchor_hands_over_its_recorded_commit(self) -> None:
        self.fx.bead("anchor", {"work_dir": str(self.fx.tmp / "reaped"), "gc.work_commit": self.fx.head, "gc.work_branch": BRANCH})
        self.fx.checks([check_run("ci-gate")])

        self.assert_pass(self.fx.run())


class BaseBranchAlsoRedTests(PrCiGreenTestCase):
    """A worker fixes what its branch broke, not what the base branch already has.

    A red GitHub Actions job passes, with a warning, only when the base branch
    does not require it AND the base branch's latest result for the same
    workflow and job is red at the same steps. A job name alone is not enough
    (fernanja/ascent_app#2517: `build (24.x)` failed at "Playwright smoke
    tests", the branch's own break, while main failed at "Run ubs").
    """

    UBS = "Run ubs (critical-only lint gate, changed lines)"
    SMOKE = "Playwright smoke tests"

    def setUp(self) -> None:
        super().setUp()
        self.fx.implementation_item()
        self.fx.pulls([self.fx.pull()])
        self.red = check_run("build (24.x)", conclusion="failure")
        self.fx.checks([check_run("ci-gate"), self.red])
        self.theirs = base_run("build (24.x)", "failure")
        self.fx.base_checks([base_run("ci-gate"), self.theirs])
        self.fx.job(self.red, failed=(self.UBS,))
        self.fx.job(self.theirs, failed=(self.UBS,))

    def test_red_at_the_same_step_as_the_base_branch_passes_with_a_warning_naming_both_runs_and_steps(self) -> None:
        result = self.fx.run()

        self.assert_pass(result)
        warning = next(line for line in result.stdout.splitlines() if "WARNING" in line)
        for fragment in (
            f"{REPO}#7: check 'build (24.x)' (failure) {self.red['html_url']} is red at step '{self.UBS}'",
            f"main at {self.fx.base[:12]} is red at step '{self.UBS}' too: {self.theirs['html_url']}",
            "It is not a required check of main, so it does not block",
            "do not fix main's failure on this branch",
        ):
            self.assertIn(fragment, warning)
        self.assertIn("checks=2 (passed 1, skipped 0, red but not blocking 1: build (24.x))", result.stdout)
        recorded = self.fx.recorded_results()
        self.assertEqual(len(recorded), 1)
        self.assertIn("gc.build.ci_gate_result=PASS", recorded[0])
        self.assertIn(self.red["html_url"], recorded[0])
        self.assertIn(self.theirs["html_url"], recorded[0])

    def test_red_at_a_different_step_than_the_base_branch_blocks_and_names_the_step(self) -> None:
        self.fx.job(self.red, failed=(self.SMOKE,))

        result = self.fx.run()

        self.assert_fail(
            result,
            "1 failed, 0 unfinished, of 2 checks",
            f"FAILED  build (24.x) (failure) {self.red['html_url']} "
            f"[fails at step '{self.SMOKE}'; main at {self.fx.base[:12]} fails at '{self.UBS}']",
            "fix what this branch broke",
        )
        self.assertNotIn("WARNING", result.stdout + result.stderr)
        self.assertEqual(self.fx.recorded_results(), [])

    def test_a_step_the_branch_broke_on_top_of_the_base_branch_s_failure_blocks(self) -> None:
        self.fx.job(self.red, failed=(self.UBS, self.SMOKE))

        self.assert_fail(self.fx.run(), f"[fails at step '{self.SMOKE}'; main at {self.fx.base[:12]} fails at '{self.UBS}']")

    def test_fewer_failed_steps_than_the_base_branch_passes(self) -> None:
        self.fx.job(self.theirs, failed=(self.UBS, "Vitest unit"))

        self.assert_pass(self.fx.run())

    def test_the_same_job_name_in_another_workflow_on_the_base_branch_does_not_excuse_it(self) -> None:
        # main's red `build (24.x)` belongs to a different workflow.
        self.fx.base_checks([base_run("ci-gate"), self.theirs], workflow_runs=[workflow_run(9, "Nightly rebuild")])
        self.fx.job(self.theirs, workflow="Nightly rebuild", failed=(self.UBS,))

        result = self.fx.run()

        self.assert_fail(result, f"FAILED  build (24.x) (failure) {self.red['html_url']} [main: no result]")
        self.assertNotIn("WARNING", result.stdout + result.stderr)

    def test_a_job_whose_steps_cannot_be_read_blocks(self) -> None:
        self.fx.route_error(f"repos/{REPO}/actions/jobs/{self.red['id']}", NOT_FOUND)

        self.assert_fail(self.fx.run(), "the job's steps could not be read to compare")

    def test_a_job_that_failed_without_a_failed_step_blocks(self) -> None:
        self.fx.job(self.red, failed=())

        self.assert_fail(self.fx.run(), "[no failed step to compare with main]")

    def test_not_required_but_green_on_the_base_head_blocks(self) -> None:
        self.fx.base_checks([base_run("ci-gate"), base_run("build (24.x)")])

        result = self.fx.run()

        self.assert_fail(
            result,
            "1 failed, 0 unfinished, of 2 checks",
            f"FAILED  build (24.x) (failure) {self.red['html_url']} [main: green at {self.fx.base[:12]}]",
        )
        self.assertNotIn("WARNING", result.stdout + result.stderr)
        self.assertEqual(self.fx.recorded_results(), [])

    def test_not_required_and_never_run_on_the_base_branch_blocks(self) -> None:
        self.fx.base_checks([base_run("ci-gate")])
        self.fx.base_history([OLDER_1])
        self.fx.base_checks([base_run("ci-gate")], sha=OLDER_1)

        result = self.fx.run()

        self.assert_fail(
            result,
            f"FAILED  build (24.x) (failure) {self.red['html_url']} [main: no result]",
            "'main: no result' means main has no completed run of that workflow's job on its head or the 10 commits before it",
        )

    def test_required_and_red_on_the_base_head_still_blocks(self) -> None:
        self.fx.base_branch(required=["ci-gate", "build (24.x)"])

        self.assert_fail(self.fx.run(), f"FAILED  build (24.x) (failure) {self.red['html_url']} [required by main]")

    def test_required_by_a_ruleset_and_red_on_the_base_head_still_blocks(self) -> None:
        self.fx.base_branch(
            required=["ci-gate"],
            rulesets=[{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "build (24.x)"}]}}],
        )

        self.assert_fail(self.fx.run(), "[required by main]")

    def test_unreadable_branch_protection_makes_every_check_required(self) -> None:
        for name, arrange in (
            ("protection hidden", lambda: self.fx.base_branch(required=None)),
            (
                "branch refused",
                lambda: self.fx.route_error(f"repos/{REPO}/branches/main", "gh: Resource not accessible by integration (HTTP 403)"),
            ),
            (
                "rulesets refused",
                lambda: self.fx.route_error(
                    f"repos/{REPO}/rules/branches/main?per_page=100", "gh: Resource not accessible by integration (HTTP 403)"
                ),
            ),
        ):
            with self.subTest(name):
                self.fx.base_branch(required=["ci-gate"])
                arrange()

                result = self.fx.run()

                self.assert_fail(
                    result,
                    f"FAILED  build (24.x) (failure) {self.red['html_url']} [counted as required]",
                    "Every check counts as required here because",
                )
                self.assertNotIn("WARNING", result.stdout + result.stderr)

    def test_an_unprotected_base_branch_requires_nothing(self) -> None:
        self.fx.base_branch(required=None, protected=False)

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn("WARNING", result.stdout)

    def test_a_job_that_did_not_run_on_the_base_head_is_judged_by_the_base_branch_s_last_run_of_it(self) -> None:
        # A path-filtered job runs only on some base commits. The head has no
        # run of it and the commit before has an unfinished one; the newest
        # completed result on main decides.
        self.fx.base_checks([base_run("ci-gate")])
        self.fx.base_history([OLDER_1, OLDER_2])
        self.fx.base_checks([base_run("ci-gate"), base_run("build (24.x)", status="in_progress")], sha=OLDER_1)
        self.fx.base_checks([base_run("ci-gate"), self.theirs], sha=OLDER_2)

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn(f"main at {OLDER_2[:12]} is red at step '{self.UBS}' too: {self.theirs['html_url']}", result.stdout)

    def test_a_base_branch_that_has_since_gone_green_blocks(self) -> None:
        self.fx.base_checks([base_run("ci-gate")])
        self.fx.base_history([OLDER_1, OLDER_2])
        self.fx.base_checks([base_run("ci-gate"), base_run("build (24.x)")], sha=OLDER_1)
        self.fx.base_checks([base_run("ci-gate"), self.theirs], sha=OLDER_2)

        self.assert_fail(self.fx.run(), f"[main: green at {OLDER_1[:12]}]")
        self.assertFalse(any(OLDER_2 in call for call in self.fx.gh_calls()), "stopped at the newest result")

    def test_lookback_zero_judges_by_the_base_head_alone(self) -> None:
        self.fx.base_checks([base_run("ci-gate")])
        self.fx.base_history([OLDER_1])
        self.fx.base_checks([self.theirs], sha=OLDER_1)

        result = self.fx.run(env={"PR_CI_GREEN_BASE_LOOKBACK": "0"})

        self.assert_fail(result, "[main: no result]")
        self.assertFalse(any(OLDER_1 in call for call in self.fx.gh_calls()))

    def test_a_tolerated_red_check_does_not_hide_one_this_branch_broke(self) -> None:
        mine = check_run("lint", conclusion="failure")
        self.fx.checks([check_run("ci-gate"), self.red, mine])
        self.fx.base_checks([base_run("ci-gate"), self.theirs, base_run("lint")])

        result = self.fx.run()

        self.assert_fail(
            result,
            "1 failed, 0 unfinished, of 3 checks",
            f"FAILED  lint (failure) {mine['html_url']} [main: green at {self.fx.base[:12]}]",
            "(red but not blocking: build (24.x))",
        )


class ExternalCheckTests(PrCiGreenTestCase):
    """A red check from another app (a deployment preview, a coverage bot) that
    the base branch does not require is not CI for the change: it is reported,
    it does not block (fernanja/ascent_app#2501, #2503: every Actions check
    green, "Vercel: Deployment was blocked")."""

    def setUp(self) -> None:
        super().setUp()
        self.fx.implementation_item()
        self.fx.pulls([self.fx.pull()])

    def test_a_red_commit_status_that_is_not_required_passes_with_a_warning(self) -> None:
        self.fx.checks(
            [check_run("ci-gate")],
            statuses=[{"context": "Vercel", "state": "failure", "target_url": "https://vercel.example.test/deploy/1"}],
        )

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn(
            f"WARNING {REPO}#7: 'Vercel' (failure) https://vercel.example.test/deploy/1 is red. "
            "It is not a GitHub Actions check and main does not require it, so it does not block; report it",
            result.stdout,
        )
        self.assertIn("checks=2 (passed 1, skipped 0, red but not blocking 1: Vercel)", result.stdout)
        self.assertIn("https://vercel.example.test/deploy/1", self.fx.recorded_results()[0])
        # Nothing on the base branch was consulted to excuse it.
        self.assertFalse(any(self.fx.base in call and "check-runs" in call for call in self.fx.gh_calls()))

    def test_a_red_check_run_from_another_app_that_is_not_required_passes_with_a_warning(self) -> None:
        preview = check_run("deploy-preview", conclusion="failure", app="netlify")
        self.fx.checks([check_run("ci-gate"), preview])

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn(f"'deploy-preview' (failure) {preview['html_url']} is red", result.stdout)

    def test_a_red_external_check_the_base_branch_requires_blocks(self) -> None:
        self.fx.base_branch(required=["ci-gate", "Vercel"])
        self.fx.checks(
            [check_run("ci-gate")],
            statuses=[{"context": "Vercel", "state": "failure", "target_url": "https://vercel.example.test/deploy/1"}],
        )

        self.assert_fail(self.fx.run(), "FAILED  Vercel (failure) https://vercel.example.test/deploy/1 [required by main]")

    def test_a_red_external_check_blocks_when_the_base_branch_s_protection_cannot_be_read(self) -> None:
        self.fx.base_branch(required=None)
        self.fx.checks(
            [check_run("ci-gate")],
            statuses=[{"context": "Vercel", "state": "error", "target_url": "https://vercel.example.test/deploy/1"}],
        )

        self.assert_fail(self.fx.run(), "FAILED  Vercel (error) https://vercel.example.test/deploy/1 [counted as required]")

    def test_an_unfinished_external_check_is_still_unfinished(self) -> None:
        self.fx.checks(
            [check_run("ci-gate")],
            statuses=[{"context": "Vercel", "state": "pending", "target_url": "https://vercel.example.test/deploy/1"}],
        )

        self.assert_fail(self.fx.run(), "PENDING Vercel (pending) https://vercel.example.test/deploy/1")


class SameNamedCheckRunTests(PrCiGreenTestCase):
    """`filter=latest` still returns one check run per workflow run, so a
    commit can carry several runs with one name. A check is a (workflow, job
    name) pair; when a pair has several runs, the latest started one counts."""

    def setUp(self) -> None:
        super().setUp()
        self.fx.implementation_item()
        self.fx.pulls([self.fx.pull()])

    def test_a_stale_cancelled_run_is_replaced_by_the_later_run_of_the_same_workflow_job(self) -> None:
        stale = check_run("prod-smoke", conclusion="cancelled", run=1, started="2026-10-01T10:00:00Z")
        fresh = check_run("prod-smoke", run=2, started="2026-10-01T11:00:00Z")
        self.fx.checks([stale, check_run("ci-gate"), fresh], workflow_runs=[workflow_run(1), workflow_run(2)])

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn("checks=2 (passed 2, skipped 0)", result.stdout)

    def test_the_later_run_decides_when_it_is_the_red_one(self) -> None:
        earlier = check_run("ci-gate", run=1, started="2026-10-01T10:00:00Z")
        later = check_run("ci-gate", conclusion="failure", run=2, started="2026-10-01T11:00:00Z")
        self.fx.checks([later, earlier], workflow_runs=[workflow_run(1), workflow_run(2)])

        self.assert_fail(self.fx.run(), "1 failed, 0 unfinished, of 1 checks", f"FAILED  ci-gate (failure) {later['html_url']}")

    def test_the_same_job_name_in_two_workflows_is_two_checks(self) -> None:
        green = check_run("changes", run=1)
        red = check_run("changes", conclusion="failure", run=2, started="2026-10-01T09:00:00Z")
        self.fx.checks(
            [check_run("ci-gate"), green, red], workflow_runs=[workflow_run(1, "CI"), workflow_run(2, "E2E Auth")]
        )

        result = self.fx.run()

        self.assert_fail(result, "1 failed, 0 unfinished, of 3 checks", f"FAILED  changes (failure) {red['html_url']}")

    def test_an_older_unfinished_run_of_a_workflow_is_not_waited_for_once_a_later_run_exists(self) -> None:
        self.fx.checks(
            [check_run("ci-gate", run=2, started="2026-10-01T11:00:00Z")],
            workflow_runs=[
                workflow_run(1, status="queued", started="2026-10-01T10:00:00Z"),
                workflow_run(2, started="2026-10-01T11:00:00Z"),
            ],
        )

        self.assert_pass(self.fx.run())


class ConflictedPullRequestTests(PrCiGreenTestCase):
    """GitHub runs no pull_request workflow on a pull request that conflicts
    with its base, so "wait for the missing checks" would never end
    (fernanja/ascent_app#2522)."""

    def setUp(self) -> None:
        super().setUp()
        self.fx.implementation_item()
        self.fx.pulls([self.fx.pull()])
        self.fx.pull_details([self.fx.pull()], mergeable=False, mergeable_state="dirty")

    def test_missing_required_checks_on_a_conflicted_pull_request_are_put_down_to_the_conflict(self) -> None:
        self.fx.base_branch(required=["ci-gate", "vitest-browser-gate"])
        self.fx.checks([check_run("changes")])

        result = self.fx.run()

        self.assert_fail(
            result,
            "MISSING ci-gate (required by main",
            "The pull request has merge conflicts with main",
            "GitHub does not run pull_request workflows on a conflicted pull request, so waiting will not help",
            "Merge main into the work branch, resolve the conflicts, push",
        )
        self.assertNotIn("Wait for the unfinished checks", result.stderr)

    def test_no_checks_at_all_on_a_conflicted_pull_request_is_put_down_to_the_conflict(self) -> None:
        self.fx.checks([], workflow_runs=[])

        self.assert_fail(self.fx.run(), "no check has reported", "The pull request has merge conflicts with main")

    def test_a_conflict_without_push_is_not_an_order_to_push(self) -> None:
        self.fx.implementation_item(push="false", open_pr="false")
        self.fx.base_branch(required=["ci-gate", "vitest-browser-gate"])
        self.fx.checks([check_run("changes")])

        result = self.fx.run()

        self.assert_fail(result, "The pull request has merge conflicts with main", "gc.failure_class=hard")
        self.assertNotIn("push", result.stderr.lower())

    def test_green_checks_on_a_conflicted_pull_request_pass_with_a_warning(self) -> None:
        self.fx.checks([check_run("ci-gate")])

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn("WARNING", result.stdout)
        self.assertIn("the checks are green but the pull request has merge conflicts with main", result.stdout)


class InfrastructureErrorTests(PrCiGreenTestCase):
    """An error that says nothing about the commit is retried and never counted as red."""

    SHORT = {"PR_CI_GREEN_INFRA_BUDGET_SECONDS": "1"}

    def setUp(self) -> None:
        super().setUp()
        self.fx.implementation_item()
        self.fx.pulls([self.fx.pull()])
        self.fx.checks([check_run("ci-gate")])

    def test_a_rate_limit_that_clears_is_retried_to_a_verdict(self) -> None:
        self.fx.route_error(self.fx.check_runs_path(), RATE_LIMITED, times=2)

        result = self.fx.run()

        self.assert_pass(result)
        self.assertEqual(sum(1 for call in self.fx.gh_calls() if "check-runs" in call and self.fx.head in call), 3)
        self.assertIn("pr-ci-green: INFRA GitHub", result.stderr)
        self.assertIn("HTTP 403", result.stderr)
        self.assertIn("retrying with backoff for up to 30s", result.stderr)

    def test_a_rate_limit_that_does_not_clear_is_no_verdict_not_a_failed_attempt(self) -> None:
        for message in (RATE_LIMITED, "gh: You have exceeded a secondary rate limit. (HTTP 403)", "gh: Too Many Requests (HTTP 429)"):
            with self.subTest(message=message):
                self.fx.route_error(self.fx.check_runs_path(), message)

                result = self.fx.run(env=self.SHORT)

                self.assert_no_verdict(result, "INFRA no verdict", "this is not a red check")
                self.assertEqual(self.fx.recorded_results(), [])

    def test_a_server_error_or_a_network_error_is_no_verdict(self) -> None:
        for message in ("gh: Server Error (HTTP 502)", "gh: Service Unavailable (HTTP 503)", "dial tcp: lookup api.github.com: no such host"):
            with self.subTest(message=message):
                self.fx.route_error(self.fx.check_runs_path(), message)

                self.assert_no_verdict(self.fx.run(env=self.SHORT), message.removeprefix("gh: "))

    def test_a_permission_refusal_is_a_failure_not_a_retry(self) -> None:
        self.fx.route_error(self.fx.check_runs_path(), "gh: Resource not accessible by integration (HTTP 403)")

        result = self.fx.run()

        self.assert_fail(result, "GitHub refused", "HTTP 403", "the gate does not pass")
        self.assertEqual(sum(1 for call in self.fx.gh_calls() if "check-runs" in call), 1)

    def test_rejected_credentials_are_no_verdict_when_the_workflow_publishes(self) -> None:
        # A gate that cannot authenticate has not checked anything. With
        # publishing intent that is an infrastructure error, never a pass.
        self.fx.route_error(self.fx.check_runs_path(), "gh: Bad credentials (HTTP 401)")

        result = self.fx.run(env=self.SHORT)

        self.assert_no_verdict(result, "GitHub rejected gh's credentials", "HTTP 401")
        self.assertEqual(self.fx.recorded_results(), [])

    def test_rejected_credentials_that_recover_reach_a_verdict(self) -> None:
        self.fx.route_error(self.fx.check_runs_path(), "gh: Bad credentials (HTTP 401)", times=1)

        self.assert_pass(self.fx.run())

    def test_rejected_credentials_without_publishing_intent_are_a_skip(self) -> None:
        self.fx.implementation_item(push="false", open_pr="false")
        self.fx.route_error(self.fx.check_runs_path(), "gh: Bad credentials (HTTP 401)")

        self.assert_skipped(self.fx.run(), "GitHub rejected gh's credentials (HTTP 401)", loud=False)

    def test_a_signed_out_gh_is_no_verdict_when_the_workflow_publishes(self) -> None:
        (self.fx.gh_dir / "signed-out").write_text("", encoding="utf-8")

        result = self.fx.run(env=self.SHORT)

        self.assert_no_verdict(result, "gh sign-in kept failing", "gh is not signed in to github.com")
        self.assertFalse(any(call.startswith("api ") for call in self.fx.gh_calls()))

    def test_a_signed_out_gh_without_publishing_intent_is_a_skip(self) -> None:
        self.fx.implementation_item(push="false", open_pr="false")
        (self.fx.gh_dir / "signed-out").write_text("", encoding="utf-8")

        self.assert_skipped(self.fx.run(), "gh is not signed in", loud=False)

    def test_a_commit_github_does_not_have_is_told_to_push_only_when_the_workflow_may_push(self) -> None:
        self.fx.route_error(self.fx.check_runs_path(), NOT_FOUND)

        with_push = self.fx.run()
        self.assert_fail(
            with_push, f"commit {self.fx.head[:12]} is not on GitHub in {REPO}: push it before handing it to review"
        )

        self.fx.implementation_item(push="false", open_pr="false")
        without_push = self.fx.run()
        self.assert_fail(without_push, f"commit {self.fx.head[:12]} is not on GitHub in {REPO}")
        self.assertNotIn("push", without_push.stderr.lower())

    def test_under_the_controller_the_gate_retries_until_the_check_timeout_ends_it(self) -> None:
        # The dispatcher counts any exit code as a failed attempt and only a
        # check still running at its timeout as "could not run". With
        # GC_ITERATION set (the controller's environment) there is no budget:
        # the script cannot know whether its check has 2, 5 or 20 minutes. The
        # reason is already on stderr when the timeout ends the process.
        self.fx.route_error(self.fx.check_runs_path(), "gh: Server Error (HTTP 502)")
        env = {"GC_ITERATION": "1"}

        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.fx.run(env={**env, "PR_CI_GREEN_INFRA_BUDGET_SECONDS": ""}, timeout=10)

        stderr = caught.exception.stderr or b""
        stderr = stderr.decode("utf-8") if isinstance(stderr, bytes) else stderr
        self.assertIn("pr-ci-green: INFRA GitHub", stderr)
        self.assertIn("HTTP 502", stderr)
        self.assertIn("retrying with backoff until the check times out", stderr)
        self.assertIn("without counting a failed attempt", stderr)
        self.assertNotIn("FAIL", stderr)

    def test_default_budget_is_unbounded_under_the_controller_and_short_by_hand(self) -> None:
        program = (
            "import importlib.util, sys;"
            "spec = importlib.util.spec_from_file_location('gate', sys.argv[1]);"
            "gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate);"
            "print(gate.UNDER_CONTROLLER, gate.INFRA_BUDGET_SECONDS)"
        )
        gate = str(PACK_ROOT / "assets" / "scripts" / "pr_ci_green.py")
        clean = {key: value for key, value in os.environ.items() if not key.startswith(("PR_CI_GREEN_", "GC_ITERATION"))}

        controller = subprocess.run(
            ["python3", "-c", program, gate], env={**clean, "GC_ITERATION": "2"}, capture_output=True, text=True, check=True
        ).stdout.split()
        by_hand = subprocess.run(["python3", "-c", program, gate], env=clean, capture_output=True, text=True, check=True).stdout.split()

        self.assertEqual(controller, ["True", "inf"])
        self.assertEqual(by_hand, ["False", "45.0"])

    def test_a_gate_left_without_a_parent_stops_retrying(self) -> None:
        # When the controller ends a wrapper script, the gate it started is
        # left running with no parent. With no budget it must still stop.
        self.fx.route_error(self.fx.check_runs_path(), "gh: Server Error (HTTP 502)")
        env = {
            "PATH": f"{self.fx.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": os.environ.get("HOME", str(self.fx.city)),
            "GH_STUB_DIR": str(self.fx.gh_dir),
            "BD_SHOW_DIR": str(self.fx.beads),
            "GC_STORE_PATH": str(self.fx.repo),
            "GC_BEAD_ID": "step",
            "GC_ITERATION": "1",
            "PR_CI_GREEN_RETRY_SLEEP_SECONDS": "0.2",
        }
        out = self.fx.tmp / "orphan.err"
        # The wrapper starts the gate, lives long enough for it to be
        # retrying, and is then gone (as when the controller ends it).
        wrapper = f'"{SCRIPT}" >/dev/null 2>"{out}" & echo $! >"{self.fx.tmp}/orphan.pid"; sleep 6'
        subprocess.run(["bash", "-c", wrapper], env=env, cwd=str(self.fx.city), check=True)
        pid = int((self.fx.tmp / "orphan.pid").read_text())

        deadline = time.monotonic() + 60
        alive = True
        while alive and time.monotonic() < deadline:
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
        if alive:
            os.kill(pid, 9)
        self.assertFalse(alive, "the orphaned gate was still retrying after 60s")
        self.assertIn("the process that started this check is gone", out.read_text(encoding="utf-8"))

    def test_a_bead_read_that_fails_and_recovers_reaches_a_verdict(self) -> None:
        (self.fx.beads / "root.error").write_text("dolt circuit breaker is open\n", encoding="utf-8")
        (self.fx.beads / "root.flaky").write_text("2", encoding="utf-8")

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn("pr-ci-green: INFRA gc bd show root failed: dolt circuit breaker is open", result.stderr)

    def test_a_bead_read_that_keeps_failing_is_no_verdict(self) -> None:
        (self.fx.beads / "root.json").unlink()
        (self.fx.beads / "root.error").write_text("events: lock timed out\n", encoding="utf-8")

        result = self.fx.run(env=self.SHORT)

        self.assert_no_verdict(result, "gc bd show root kept failing: events: lock timed out")
        self.assertEqual(self.fx.gh_calls(), [])

    def test_a_bead_that_does_not_exist_is_a_failure(self) -> None:
        self.fx.bead("step", {"gc.root_bead_id": "gone"})

        self.assert_fail(self.fx.run(), "bead gone does not exist")


class PublishingIntentTests(PrCiGreenTestCase):
    def test_no_publishing_intent_and_no_handoff_skips_without_asking_github(self) -> None:
        for push, open_pr in (("false", "false"), ("true", "false"), ("false", "true"), ("", "")):
            with self.subTest(push=push, open_pr=open_pr):
                self.fx.fix_loop(push=push, open_pr=open_pr)

                result = self.fx.run()

                self.assert_skipped(result, "no publishing intent", loud=False)
                self.assertIn(f"push={push or 'unset'} open_pr={open_pr or 'unset'}", result.stdout)
                self.assertEqual(self.fx.gh_calls(), [])

    def test_no_publishing_intent_and_no_pull_request_skips(self) -> None:
        self.fx.implementation_item(push="false", open_pr="false")
        self.fx.pulls([])
        self.fx.commit_pulls(None)

        self.assert_skipped(self.fx.run(), f"no open pull request for branch {BRANCH}", loud=False)

    def test_an_existing_pull_request_is_checked_even_without_publishing_intent(self) -> None:
        self.fx.implementation_item(push="false", open_pr="false")
        self.fx.pulls([self.fx.pull()])
        self.fx.checks([check_run("ci-gate", conclusion="failure")])

        result = self.fx.run()

        self.assert_fail(result, "FAILED  ci-gate (failure)", "gc.outcome=fail and gc.failure_class=hard")
        # The workflow was told not to push: the gate must not tell it to.
        self.assertNotIn("push", result.stderr.lower())

    def test_without_push_a_pull_request_at_another_commit_is_a_loud_skip_not_an_order_to_push(self) -> None:
        other = "5555555555555555555555555555555555555555"
        self.fx.implementation_item(push="false", open_pr="false")
        self.fx.pulls([self.fx.pull(head=other)])

        result = self.fx.run()

        self.assert_skipped(result, f"handoff commit {self.fx.head[:12]} is local only", loud=False)
        self.assertIn(f"{REPO}#7 is at {other[:12]}", result.stdout)
        for stream in (result.stdout, result.stderr):
            self.assertNotIn("push the", stream.lower())
            self.assertNotIn("push it", stream.lower())
        self.assertIn("gc.build.ci_gate_result=skipped: push=false open_pr=false", self.fx.recorded_results()[0])

    def test_without_push_a_recorded_commit_that_is_not_the_worktree_head_is_not_told_to_push(self) -> None:
        self.fx.implementation_item(push="false", open_pr="false", root_extra={"gc.build.handoff_commit": self.fx.base})

        result = self.fx.run()

        self.assert_fail(result, "gc.build.handoff_commit on root", "Record the commit that is actually handed over")
        self.assertNotIn("push", result.stderr.lower())

    def test_publishing_intent_without_a_recorded_handoff_fails(self) -> None:
        self.fx.fix_loop()

        self.assert_fail(self.fx.run(), "intends to publish (push=true open_pr=true)", "gc.build.handoff_commit")


class FixLoopHandoffTests(PrCiGreenTestCase):
    """The gate on apply-fixes: the worker records the commit it hands to re-review."""

    def test_recorded_commit_at_the_pull_request_head_with_green_checks_passes(self) -> None:
        self.fx.fix_loop(commit=self.fx.head, branch=BRANCH)
        self.fx.pulls([self.fx.pull()])
        self.fx.checks([check_run("ci-gate")])

        result = self.fx.run()

        self.assert_pass(result)
        self.assertIn("pr_state=draft", result.stdout)

    def test_a_pull_request_marked_ready_before_re_review_fails(self) -> None:
        self.fx.fix_loop(commit=self.fx.head, branch=BRANCH)
        self.fx.pulls([self.fx.pull(draft=False)])
        self.fx.checks([check_run("ci-gate")])

        self.assert_fail(self.fx.run(), "is marked ready for review", f"gh pr ready --undo 7 --repo {REPO}")

    def test_recorded_commit_behind_the_pull_request_head_fails(self) -> None:
        newer = "3333333333333333333333333333333333333333"
        self.fx.fix_loop(commit=self.fx.head, branch=BRANCH)
        self.fx.pulls([self.fx.pull(head=newer)])

        self.assert_fail(self.fx.run(), "head mismatch", newer[:12], self.fx.head[:12])

    def test_recorded_commit_without_a_branch_finds_the_pull_request_by_commit(self) -> None:
        self.fx.fix_loop(commit=self.fx.head)
        self.fx.commit_pulls([self.fx.pull(number=2, head=self.fx.head, state="closed"), self.fx.pull(number=7)])
        self.fx.checks([check_run("ci-gate", status="in_progress", conclusion=None)])

        self.assert_fail(self.fx.run(), f"{REPO}#7 head {self.fx.head[:12]}", "PENDING ci-gate")

    def test_recorded_branch_that_has_no_pull_request_falls_back_to_the_commit(self) -> None:
        self.fx.fix_loop(commit=self.fx.head, branch="local-name-only")
        self.fx.pulls([], branch="local-name-only")
        self.fx.commit_pulls([self.fx.pull(number=7)])
        self.fx.checks([check_run("ci-gate")])

        self.assert_pass(self.fx.run())

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

        self.assert_skipped(result, "origin is not a github.com remote", loud=True)
        self.assertNotIn("secret", result.stdout + result.stderr)
        self.assertEqual(self.fx.gh_calls(), [])

    def test_an_ssh_host_alias_remote_is_skipped_loudly_when_the_workflow_publishes(self) -> None:
        self.fx.git("remote", "set-url", "origin", f"git@github-work:{REPO}.git")

        result = self.fx.run()

        self.assert_skipped(result, "an SSH host alias for github.com is not recognised", loud=True)
        self.assertIn("push=true open_pr=true", result.stderr)
        recorded = self.fx.recorded_results()
        self.assertEqual(len(recorded), 1)
        self.assertIn("root --set-metadata gc.build.ci_gate_result=skipped: origin is not a github.com remote", recorded[0])

    def test_the_same_skip_without_publishing_intent_is_quiet(self) -> None:
        self.fx.implementation_item(push="false", open_pr="false")
        self.fx.git("remote", "set-url", "origin", f"git@github-work:{REPO}.git")

        self.assert_skipped(self.fx.run(), "origin is not a github.com remote", loud=False)

    def test_repository_without_a_remote_is_an_explicit_skip(self) -> None:
        self.fx.git("remote", "remove", "origin")

        self.assert_skipped(self.fx.run(), "no git origin remote", loud=True)

    def test_missing_gh_is_an_explicit_skip(self) -> None:
        (self.fx.bin / "gh").unlink()

        result = self.fx.run(path=f"{self.fx.bin}:/usr/bin:/bin", env={"PR_CI_GREEN_GH_FALLBACK_PATHS": ""})

        self.assert_skipped(result, "gh is not installed", loud=True)

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

    def test_manual_mode_does_not_require_a_draft(self) -> None:
        self.fx.route(f"repos/{REPO}/pulls/7", self.fx.pull(draft=False))
        self.fx.checks([check_run("ci-gate")])

        result = self.fx.run("--repo", REPO, "--pr", "7", bead=None)

        self.assert_pass(result)
        self.assertIn("pr_state=open", result.stdout)

    def test_merged_pull_request_needs_any_state(self) -> None:
        self.fx.route(f"repos/{REPO}/pulls/7", self.fx.pull(state="closed", draft=False))
        self.fx.checks([check_run("ci-gate")])

        self.assert_fail(self.fx.run("--repo", REPO, "--pr", "7", bead=None), "is closed, not open")
        self.assert_pass(self.fx.run("--repo", REPO, "--pr", "7", "--any-state", bead=None))

    def test_any_state_inspects_an_older_head_of_the_pull_request(self) -> None:
        older = "4444444444444444444444444444444444444444"
        self.fx.route(f"repos/{REPO}/pulls/7", self.fx.pull(state="closed", draft=False))
        self.fx.checks([check_run("ci-gate"), check_run("build (24.x)", conclusion="failure")], sha=older)

        result = self.fx.run("--repo", REPO, "--pr", "7", "--commit", older, "--any-state", bead=None)

        self.assert_fail(result, f"head {older[:12]}: 1 failed", "FAILED  build (24.x) (failure)")

    def test_commit_with_any_state_inspects_a_commit_of_a_merged_pull_request_that_is_not_its_head(self) -> None:
        # The merge commit of a merged pull request, or one of its older pushes.
        merge = "6666666666666666666666666666666666666666"
        self.fx.commit_pulls([self.fx.pull(state="closed", draft=False)], sha=merge)
        self.fx.checks([check_run("ci-gate")], sha=merge)

        result = self.fx.run("--repo", REPO, "--commit", merge, "--any-state", bead=None)

        self.assert_pass(result)
        self.assertIn(f"{REPO}#7 (commit given; the head is {self.fx.head[:12]}) head={merge}", result.stdout)

    def test_commit_without_any_state_must_be_the_head_of_an_open_pull_request(self) -> None:
        merge = "6666666666666666666666666666666666666666"
        self.fx.commit_pulls([self.fx.pull(state="closed", draft=False)], sha=merge)

        self.assert_fail(self.fx.run("--repo", REPO, "--commit", merge, bead=None), f"no open pull request contains commit {merge[:12]}")

    def test_a_short_sha_is_resolved_before_the_workflow_runs_are_listed(self) -> None:
        self.fx.route(f"repos/{REPO}/commits/{self.fx.head[:9]}", {"sha": self.fx.head})
        self.fx.commit_pulls([self.fx.pull(state="closed", draft=False)])
        self.fx.checks([check_run("ci-gate")])

        result = self.fx.run("--repo", REPO, "--commit", self.fx.head[:9], "--any-state", bead=None)

        self.assert_pass(result)
        self.assertTrue(any(f"actions/runs?head_sha={self.fx.head}&" in call for call in self.fx.gh_calls()))

    def test_commit_with_any_state_and_no_pull_request_is_judged_against_the_default_branch(self) -> None:
        lone = "7777777777777777777777777777777777777777"
        self.fx.commit_pulls([], sha=lone)
        self.fx.checks([check_run("ci-gate")], sha=lone)

        result = self.fx.run("--repo", REPO, "--commit", lone, "--any-state", bead=None)

        self.assert_pass(result)
        self.assertIn(f"{REPO} commit head={lone}", result.stdout)


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
        self.fx.commit_pulls(None)

        result = self.fx.run(script=HANDOFF_SCRIPT)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pr-ci-green: skipped: no publishing intent", result.stdout)

    def test_no_verdict_from_the_ci_gate_is_passed_through_not_turned_into_a_failure(self) -> None:
        self.fx.checks([check_run("ci-gate")])
        self.fx.route_error(self.fx.check_runs_path(), "gh: Server Error (HTTP 502)")

        result = self.fx.run(script=HANDOFF_SCRIPT, env={"PR_CI_GREEN_INFRA_BUDGET_SECONDS": "1"})

        self.assertEqual(result.returncode, NO_VERDICT, result.stdout + result.stderr)
        self.assertIn("pr-ci-green: INFRA", result.stderr)


if __name__ == "__main__":
    unittest.main()
