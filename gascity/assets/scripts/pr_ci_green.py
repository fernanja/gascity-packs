#!/usr/bin/env python3
"""Decide whether the commit being handed to review has green GitHub checks.

Run by checks/pr-ci-green.sh. Two ways in:

  gate mode    GC_BEAD_ID names the step being closed. The workflow root it
               belongs to says whether the workflow intends to publish
               (gc.var.push and gc.var.open_pr) and which commit is handed over
               (gc.build.handoff_commit / gc.build.handoff_branch, or the head
               of the source anchor's worktree for an implementation item).
  manual mode  --repo OWNER/REPO with --pr N and/or --commit SHA. Read-only
               diagnostics for a person or a worker; no bead is read.

What green means, for the commit at the pull request head:

- Every check run and commit status is complete, no workflow run for the
  commit is still queued or in progress, and every status check the base
  branch requires has reported. A job behind `needs:` has no check run until
  it is queued, so "every check that exists is complete" alone can be true
  while CI is still running.
- Each check concluded success, skipped or neutral. Anything else is red,
  including a cancelled or timed-out job: this script does not classify
  flakes.
- One exception, because a worker cannot fix the base branch from its own
  branch: a red check passes, with a WARNING naming both runs, when it is NOT
  one of the base branch's required status checks AND the base branch's own
  most recent completed result for the same check name is also red. A red
  check that the base branch requires, that is green on the base branch, or
  that the base branch has never run, blocks. If the base branch's
  protection cannot be read, every check is treated as required.
- In gate mode the pull request must still be a draft. A ready, green,
  unreviewed pull request can be merged by a merge sweep before review; only
  the publish step marks it ready.

The script never waits for CI: the worker does that before closing its step,
and this confirms the result in a few API calls, because the controller that
runs step checks processes one control bead at a time.

Exit 0: green, or an explicit `skipped: <reason>` (no publishing intent, no
GitHub remote, gh missing or not signed in, repository without CI). A skip in
a workflow that intends to publish is also written to stderr.
Exit 1: red, pending, no pull request, not a draft, head mismatch. Detail goes
to stderr, which the dispatcher records in gc.attempt_log for the next attempt.
No verdict (GitHub 5xx, rate limit, network, `gc bd show` failing): retried
with backoff. The dispatcher counts every exit code as a failed attempt and
only a check that is still running at its timeout as "could not run"
(gascity internal/dispatch/ralph.go, GateTimeout), so under the controller the
script keeps retrying until that timeout ends it, having already said why on
stderr. Run by hand it gives up after a short budget and exits 75.

In gate mode the result line is also recorded, best effort, on the workflow
root as gc.build.ci_gate_result.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from typing import Any, Callable

PREFIX = "pr-ci-green"
GREEN_CONCLUSIONS = {"success", "skipped", "neutral"}
MAX_LISTED = 25
# The dispatcher keeps 4096 bytes of stderr; leave room for the advice that
# follows the list.
MAX_LISTED_BYTES = 3200
EXIT_NO_VERDICT = 75  # EX_TEMPFAIL
RESULT_METADATA_KEY = "gc.build.ci_gate_result"
# Where gh usually lives when the controller's narrow PATH does not include it.
# PR_CI_GREEN_GH_FALLBACK_PATHS (os.pathsep-separated, may be empty) replaces
# the list; tests use it to simulate a host without gh.
GH_FALLBACK_PATHS = tuple(
    path
    for path in os.environ.get(
        "PR_CI_GREEN_GH_FALLBACK_PATHS",
        os.pathsep.join(("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh")),
    ).split(os.pathsep)
    if path
)
GH_CALL_TIMEOUT_SECONDS = 60
GC_CALL_TIMEOUT_SECONDS = 120
GC_RECORD_TIMEOUT_SECONDS = 30


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except ValueError:
        return default


STARTED = time.monotonic()
# The controller exports GC_ITERATION to a step check; a person or a worker
# running the gate by hand does not have it.
UNDER_CONTROLLER = bool(os.environ.get("GC_ITERATION", "").strip())
# How long to keep retrying an infrastructure error, measured from script
# start. Under the controller this must outlast the check timeout the formulas
# declare ("5m"), so that the dispatcher sees a timeout, which consumes no
# attempt, and not an exit code, which does.
CONTROLLER_CHECK_TIMEOUT_SECONDS = 300
INFRA_BUDGET_SECONDS = _env_float(
    "PR_CI_GREEN_INFRA_BUDGET_SECONDS", CONTROLLER_CHECK_TIMEOUT_SECONDS + 30 if UNDER_CONTROLLER else 45
)
RETRY_FIRST_SLEEP_SECONDS = _env_float("PR_CI_GREEN_RETRY_SLEEP_SECONDS", 2.0)
RETRY_MAX_SLEEP_SECONDS = 30.0
# How many base-branch commits behind its head to look for the base branch's
# own last result of a check that did not run on the head (a path-filtered
# job). 0 looks at the head commit only.
try:
    BASE_LOOKBACK_COMMITS = max(0, int(os.environ.get("PR_CI_GREEN_BASE_LOOKBACK", "10")))
except ValueError:
    BASE_LOOKBACK_COMMITS = 10

GITHUB_REMOTE_RE = re.compile(
    r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$"
)
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
RATE_LIMIT_RE = re.compile(r"rate limit|secondary rate|abuse detection|HTTP 429", re.IGNORECASE)
BEAD_MISSING_RE = re.compile(r"no issues? found", re.IGNORECASE)


class GateFailure(Exception):
    """The gate ran and the answer is no."""


class NotOnGitHub(GateFailure):
    """GitHub does not know the object asked for (HTTP 404 or 422)."""


class Refused(GateFailure):
    """GitHub refused to show the object (HTTP 403 that is not a rate limit)."""


class Transient(Exception):
    """One call failed for a reason that says nothing about the commit."""


class NoVerdict(Exception):
    """Infrastructure kept failing for the whole retry budget."""


class Skip(Exception):
    """The gate does not apply; the reason is recorded and the step passes."""

    def __init__(self, reason: str, *, loud: bool = False) -> None:
        super().__init__(reason)
        self.loud = loud


def say(message: str) -> None:
    print(f"{PREFIX}: {message}", flush=True)


def warn(message: str) -> None:
    print(f"{PREFIX}: {message}", file=sys.stderr, flush=True)


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


def retry_infra(what: str, call: Callable[[], Any]) -> Any:
    """Run call() until it stops raising Transient or the budget is spent."""
    delay = RETRY_FIRST_SLEEP_SECONDS
    announced = False
    while True:
        try:
            return call()
        except Transient as exc:
            last = str(exc)
        remaining = INFRA_BUDGET_SECONDS - (time.monotonic() - STARTED)
        if remaining <= 0:
            raise NoVerdict(f"{what} kept failing: {last}")
        if not announced:
            # Said now, not at the end: under the controller the check timeout
            # ends this process before it could say anything later.
            warn(
                f"INFRA {what} failed: {last}. This says nothing about the commit, so there is no verdict yet; "
                "retrying with backoff. If it is still failing when the check times out, the controller runs the "
                "check again without counting a failed attempt"
            )
            announced = True
        time.sleep(max(0.0, min(delay, remaining)))
        delay = min(max(delay, 0.01) * 2, RETRY_MAX_SLEEP_SECONDS)


# --------------------------------------------------------------------------
# beads


def bead_show(bead_id: str) -> dict[str, Any]:
    if shutil.which("gc") is None:
        raise GateFailure("gc is required on PATH to read the workflow beads")

    def call() -> dict[str, Any]:
        try:
            proc = run(["gc", "bd", "show", bead_id, "--json"], timeout=GC_CALL_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Transient(str(exc)) from exc
        if proc.returncode != 0:
            detail = (proc.stderr.strip() or proc.stdout.strip())[:300]
            if BEAD_MISSING_RE.search(proc.stderr + proc.stdout):
                raise GateFailure(f"bead {bead_id} does not exist: {detail}")
            raise Transient(detail or f"exit {proc.returncode}")
        try:
            data = json.loads(proc.stdout)
        except ValueError as exc:
            raise Transient("output was not JSON") from exc
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            raise Transient("output had an unexpected shape")
        return data

    return retry_infra(f"gc bd show {bead_id}", call)


def metadata(bead: dict[str, Any]) -> dict[str, str]:
    raw = bead.get("metadata") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, bool):
            out[str(key)] = "true" if value else "false"
        elif value is not None:
            out[str(key)] = str(value)
    return out


def is_true(value: str) -> bool:
    return value.strip().lower() == "true"


def record_result(root_id: str, line: str) -> None:
    """Best effort: keep the gate's last result on the workflow root."""
    if not root_id or shutil.which("gc") is None:
        return
    try:
        run(
            ["gc", "bd", "update", root_id, "--set-metadata", f"{RESULT_METADATA_KEY}={line[:2000]}"],
            timeout=GC_RECORD_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        pass


# --------------------------------------------------------------------------
# git


def git(args: list[str], cwd: str) -> str:
    try:
        proc = run(["git", "-C", cwd, *args], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def parse_github_remote(url: str) -> str:
    match = GITHUB_REMOTE_RE.match(url.strip())
    return f"{match.group('owner')}/{match.group('repo')}" if match else ""


def resolve_repo(candidates: list[str]) -> str:
    """Return OWNER/REPO for the first candidate directory with an origin remote."""
    seen_remote = ""
    for directory in candidates:
        if not directory or not os.path.isdir(directory):
            continue
        url = git(["remote", "get-url", "origin"], directory)
        if not url:
            continue
        repo = parse_github_remote(url)
        if repo:
            return repo
        seen_remote = seen_remote or url
    if seen_remote:
        raise Skip(
            f"origin is not a github.com remote ({redact_url(seen_remote)}); "
            "an SSH host alias for github.com is not recognised, use a github.com URL"
        )
    raise Skip("no git origin remote found for this workflow")


def redact_url(url: str) -> str:
    return re.sub(r"//[^@/]+@", "//", url)


# --------------------------------------------------------------------------
# gh


class GitHub:
    def __init__(self) -> None:
        self.binary = shutil.which("gh") or next((p for p in GH_FALLBACK_PATHS if os.access(p, os.X_OK)), "")
        if not self.binary:
            raise Skip("gh is not installed")
        self.env = dict(os.environ)
        self.env.update({"GH_PROMPT_DISABLED": "1", "NO_COLOR": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
        if not self._signed_in():
            # A controller runs checks with HOME pointed at the city, where gh
            # has neither its config nor (on macOS) its keychain entry. The
            # login lives in the account's real home directory.
            real_home = ""
            try:
                real_home = pwd.getpwuid(os.getuid()).pw_dir
            except (KeyError, OSError):
                pass
            if real_home and real_home != self.env.get("HOME", ""):
                self.env["HOME"] = real_home
                self.env.pop("XDG_CONFIG_HOME", None)
            if not self._signed_in():
                raise Skip("gh is not signed in to github.com")

    def _signed_in(self) -> bool:
        try:
            proc = run([self.binary, "auth", "token", "--hostname", "github.com"], env=self.env, timeout=GH_CALL_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0 and bool(proc.stdout.strip())

    def api(self, path: str) -> Any:
        def call() -> Any:
            try:
                proc = run([self.binary, "api", path], env=self.env, timeout=GH_CALL_TIMEOUT_SECONDS)
            except (OSError, subprocess.SubprocessError) as exc:
                raise Transient(str(exc)) from exc
            if proc.returncode == 0:
                try:
                    return json.loads(proc.stdout)
                except ValueError as exc:
                    raise Transient("response was not JSON") from exc
            detail = (proc.stderr.strip() or proc.stdout.strip())[:300]
            everything = proc.stderr + proc.stdout
            if RATE_LIMIT_RE.search(everything):
                raise Transient(detail)
            if "HTTP 404" in everything or "HTTP 422" in everything:
                raise NotOnGitHub(f"GitHub does not have {path}: {detail}")
            if "HTTP 401" in everything:
                raise Skip(f"GitHub rejected gh's credentials (HTTP 401) reading {path}", loud=True)
            if "HTTP 403" in everything:
                raise Refused(f"GitHub refused {path}: {detail}. The result is unknown, so the gate does not pass")
            # 5xx, a network error, a gh crash: nothing was learned.
            raise Transient(detail or f"gh exited {proc.returncode}")

        return retry_infra(f"GitHub ({path})", call)


def quote_ref(ref: str) -> str:
    return urllib.parse.quote(ref, safe="/")


# --------------------------------------------------------------------------
# evaluation


def same_sha(left: str, right: str) -> bool:
    left, right = left.strip().lower(), right.strip().lower()
    if not left or not right:
        return False
    return left.startswith(right) or right.startswith(left)


def pulls_for_commit(gh: GitHub, repo: str, commit: str, any_state: bool) -> list[dict[str, Any]]:
    try:
        found = gh.api(f"repos/{repo}/commits/{commit}/pulls?per_page=100")
    except NotOnGitHub:
        return []  # the commit was never pushed, so no pull request carries it
    pulls = [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []
    if not any_state:
        pulls = [item for item in pulls if item.get("state") == "open"]
    return pulls


def find_pull(
    gh: GitHub, repo: str, *, number: str = "", branch: str = "", commit: str = "", any_state: bool = False, first_of_many: bool = False
) -> dict[str, Any] | None:
    if number:
        pull = gh.api(f"repos/{repo}/pulls/{number}")
        if not isinstance(pull, dict):
            raise GateFailure(f"pull request #{number} in {repo} could not be read")
        return pull
    pulls: list[dict[str, Any]] = []
    if branch:
        owner = repo.split("/", 1)[0]
        state = "all" if any_state else "open"
        head = urllib.parse.quote(f"{owner}:{branch}", safe=":/")
        found = gh.api(f"repos/{repo}/pulls?head={head}&state={state}&per_page=100")
        pulls = [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []
    if not pulls and commit:
        # No branch recorded, or the local branch is not named like the branch
        # it was pushed to: the commit itself says which pull request has it.
        pulls = pulls_for_commit(gh, repo, commit, any_state)
    if not pulls:
        return None
    if len(pulls) > 1:
        exact = [item for item in pulls if commit and same_sha(str((item.get("head") or {}).get("sha", "")), commit)]
        if len(exact) == 1:
            return exact[0]
        if first_of_many:
            return (exact or pulls)[0]
        listed = ", ".join(f"#{item.get('number')} {item.get('html_url', '')}" for item in pulls[:5])
        raise GateFailure(f"more than one pull request matches the handoff in {repo}: {listed}. Close the extra one so one pull request carries this work")
    return pulls[0]


def collect_checks(gh: GitHub, repo: str, sha: str, *, no_push_advice: bool = False) -> list[dict[str, str]]:
    """Every check run and commit status on sha, as {name, state, detail, url}.

    state is one of ok, skipped, failed, pending.
    """
    checks: list[dict[str, str]] = []
    for page in range(1, 11):
        try:
            data = gh.api(f"repos/{repo}/commits/{sha}/check-runs?filter=latest&per_page=100&page={page}")
        except NotOnGitHub:
            advice = "" if no_push_advice else ": push it before handing it to review"
            raise GateFailure(f"commit {sha[:12]} is not on GitHub in {repo}{advice}") from None
        runs = data.get("check_runs") if isinstance(data, dict) else None
        if not isinstance(runs, list):
            raise GateFailure(f"GitHub returned no check_runs list for {sha}")
        for item in runs:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "")
            conclusion = str(item.get("conclusion") or "")
            if status != "completed":
                state, detail = "pending", status or "not started"
            elif conclusion in GREEN_CONCLUSIONS:
                state, detail = ("skipped" if conclusion == "skipped" else "ok"), conclusion
            else:
                state, detail = "failed", conclusion or "no conclusion"
            checks.append(
                {
                    "name": str(item.get("name") or "(unnamed check)"),
                    "state": state,
                    "detail": detail,
                    "url": str(item.get("html_url") or item.get("details_url") or ""),
                }
            )
        if len(runs) < 100:
            break
    combined = gh.api(f"repos/{repo}/commits/{sha}/status?per_page=100")
    statuses = combined.get("statuses") if isinstance(combined, dict) else None
    for item in statuses if isinstance(statuses, list) else []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("state") or "")
        state = {"success": "ok", "pending": "pending"}.get(raw, "failed")
        checks.append(
            {
                "name": str(item.get("context") or "(unnamed status)"),
                "state": state,
                "detail": raw or "no state",
                "url": str(item.get("target_url") or ""),
            }
        )
    return checks


def unfinished_workflow_runs(gh: GitHub, repo: str, sha: str) -> list[dict[str, str]]:
    """Workflow runs for sha that are queued, waiting or in progress."""
    try:
        data = gh.api(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100")
    except NotOnGitHub:
        return []  # Actions is not enabled for this repository
    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    out: list[dict[str, str]] = []
    for item in runs if isinstance(runs, list) else []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        if status == "completed":
            continue
        out.append(
            {
                "name": f"workflow run: {item.get('name') or '(unnamed workflow)'}",
                "state": "pending",
                "detail": status or "not started",
                "url": str(item.get("html_url") or ""),
            }
        )
    return out


class BaseBranch:
    """What the pull request's base branch requires, and its own results."""

    def __init__(self, gh: GitHub, repo: str, ref: str) -> None:
        self.gh, self.repo, self.ref = gh, repo, ref
        self.sha = ""
        # None: protection could not be read, so every check counts as required.
        self.required: set[str] | None = None
        self.why_unknown = ""
        self._history: list[tuple[str, list[dict[str, str]]]] = []
        self._older: list[str] | None = None
        if ref:
            self._read_policy()
        else:
            self.why_unknown = "the base branch is unknown"

    def _read_policy(self) -> None:
        try:
            branch = self.gh.api(f"repos/{self.repo}/branches/{quote_ref(self.ref)}")
        except (NotOnGitHub, Refused) as exc:
            self.why_unknown = f"branch {self.ref} could not be read ({str(exc)[:120]})"
            return
        if not isinstance(branch, dict):
            self.why_unknown = f"branch {self.ref} could not be read"
            return
        self.sha = str((branch.get("commit") or {}).get("sha") or "")
        required: set[str] | None = None
        if branch.get("protected") is False:
            required = set()
        elif branch.get("protected") is True:
            rules = (branch.get("protection") or {}).get("required_status_checks")
            if isinstance(rules, dict) and isinstance(rules.get("contexts"), list):
                required = {str(context) for context in rules["contexts"]}
                for check in rules.get("checks") or []:
                    if isinstance(check, dict) and check.get("context"):
                        required.add(str(check["context"]))
        if required is None:
            self.why_unknown = f"branch protection of {self.ref} is not readable with this login"
            return
        # Repository rulesets can require status checks as well.
        try:
            rulesets = self.gh.api(f"repos/{self.repo}/rules/branches/{quote_ref(self.ref)}?per_page=100")
        except NotOnGitHub:
            rulesets = []  # no rulesets API on this host
        except Refused:
            self.why_unknown = f"the rulesets of {self.ref} are not readable with this login"
            return
        for rule in rulesets if isinstance(rulesets, list) else []:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            for check in (rule.get("parameters") or {}).get("required_status_checks") or []:
                if isinstance(check, dict) and check.get("context"):
                    required.add(str(check["context"]))
        self.required = required

    def is_required(self, name: str) -> bool:
        return self.required is None or name in self.required

    def last_result(self, name: str) -> dict[str, str] | None:
        """The base branch's most recent completed result for a check name.

        Looks at the base head first, then, when the check did not run there
        or has not finished, at up to BASE_LOOKBACK_COMMITS commits behind it.
        Returns {state, detail, url, sha}, or None when the base branch has
        no completed result for that name in the commits looked at.
        """
        if not self.sha:
            return None
        index = 0
        while True:
            if index >= len(self._history):
                if index == 0:
                    sha = self.sha
                else:
                    if self._older is None:
                        self._older = self._older_commits()
                    if index - 1 >= len(self._older):
                        return None
                    sha = self._older[index - 1]
                self._history.append((sha, collect_checks(self.gh, self.repo, sha, no_push_advice=True)))
            sha, checks = self._history[index]
            named = [check for check in checks if check["name"] == name and check["state"] != "pending"]
            if named:
                # One red run among same-named runs is red, as it is on the
                # pull request side.
                worst = next((check for check in named if check["state"] == "failed"), named[0])
                return {**worst, "sha": sha}
            index += 1

    def _older_commits(self) -> list[str]:
        if BASE_LOOKBACK_COMMITS <= 0:
            return []
        try:
            commits = self.gh.api(
                f"repos/{self.repo}/commits?sha={quote_ref(self.ref)}&per_page={BASE_LOOKBACK_COMMITS + 1}"
            )
        except (NotOnGitHub, Refused):
            return []
        shas = [str(item.get("sha") or "") for item in commits if isinstance(item, dict)] if isinstance(commits, list) else []
        return [sha for sha in shas if sha and sha != self.sha][:BASE_LOOKBACK_COMMITS]


def evaluate(gh: GitHub, repo: str, sha: str, label: str, *, base_ref: str, may_push: bool = True, notes: list[str] | None = None) -> str:
    """Return the PASS line, or raise. Warnings printed on the way are appended to notes."""
    notes = notes if notes is not None else []
    checks = collect_checks(gh, repo, sha, no_push_advice=not may_push)
    running = unfinished_workflow_runs(gh, repo, sha)
    if not checks and not running:
        workflows = gh.api(f"repos/{repo}/actions/workflows?per_page=1")
        if isinstance(workflows, dict) and workflows.get("total_count") == 0:
            raise Skip(f"{repo} has no workflows and no check has reported on {sha[:12]} ({label})")
        raise GateFailure(
            f"no check has reported on {sha[:12]} ({label}) yet. CI has not started or has not registered: "
            "wait for the checks to appear and finish, then close the step again"
        )

    base = BaseBranch(gh, repo, base_ref)
    names = {check["name"] for check in checks}
    missing = sorted(base.required - names) if base.required else []
    pending = [check for check in checks if check["state"] == "pending"] + running

    blocking: list[tuple[dict[str, str], str]] = []
    tolerated: list[tuple[dict[str, str], dict[str, str]]] = []
    for check in (check for check in checks if check["state"] == "failed"):
        if base.required is None:
            blocking.append((check, "counted as required"))
            continue
        if base.is_required(check["name"]):
            blocking.append((check, f"required by {base_ref}"))
            continue
        theirs = base.last_result(check["name"])
        if theirs is None:
            blocking.append((check, f"{base_ref}: no result"))
        elif theirs["state"] == "failed":
            tolerated.append((check, theirs))
        else:
            blocking.append((check, f"{base_ref}: green at {theirs['sha'][:12]}"))

    warnings = [
        f"WARNING {label}: check '{check['name']}' is red on this commit ({check['detail']}) {check['url']} "
        f"and also red on {base_ref} at {theirs['sha'][:12]} ({theirs['detail']}) {theirs['url']}. "
        f"It is not a required check of {base_ref}, so it does not block; report it, do not fix {base_ref}'s failure on this branch"
        for check, theirs in tolerated
    ]

    if blocking or pending or missing:
        lines = [
            f"{label} head {sha[:12]}: {len(blocking)} failed, {len(pending) + len(missing)} unfinished, of {len(checks)} checks"
        ]
        listed = (
            [f"  FAILED  {check['name']} ({check['detail']}) {check['url']} [{why}]" for check, why in blocking]
            + [f"  PENDING {check['name']} ({check['detail']}) {check['url']}".rstrip() for check in pending]
            + [f"  MISSING {name} (required by {base_ref}, has not reported on this commit)" for name in missing]
        )
        shown, used = 0, 0
        for line in listed[:MAX_LISTED]:
            used += len(line.encode("utf-8")) + 1
            if used > MAX_LISTED_BYTES:
                break
            lines.append(line)
            shown += 1
        if len(listed) > shown:
            lines.append(f"  ... and {len(listed) - shown} more")
        for check, theirs in tolerated[:3]:
            lines.append(f"  (not blocking: {check['name']} is also red on {base_ref} at {theirs['sha'][:12]})")
        if base.required is None and blocking:
            lines.append(f"Every check counts as required here because {base.why_unknown}.")
        elif any(why.endswith("no result") for _, why in blocking):
            lines.append(
                f"'{base_ref}: no result' means {base_ref} has no completed run of that check on its head"
                + (f" or the {BASE_LOOKBACK_COMMITS} commits before it" if BASE_LOOKBACK_COMMITS else "")
                + ", so the failure cannot be put down to it."
            )
        if blocking and may_push:
            lines.append(
                "Read each failed job's log, fix what this branch broke, push, and wait for the new run. "
                "A job that died in CI's own machinery is still a failed check (one rerun is allowed). If it cannot be made green here, close the step with "
                "gc.outcome=fail and gc.failure_class=hard, naming these checks, instead of weakening a test."
            )
        elif blocking:
            lines.append(
                "This workflow was not launched to publish, so this step cannot fix the pull request. Close the step with "
                "gc.outcome=fail and gc.failure_class=hard, naming these checks, so a person decides."
            )
        else:
            lines.append("Wait for the unfinished checks and workflow runs to complete, then close the step again.")
        raise GateFailure("\n".join(lines))

    for line in warnings:
        say(line)
    skipped = sum(1 for check in checks if check["state"] == "skipped")
    counts = f"passed {len(checks) - skipped - len(tolerated)}, skipped {skipped}"
    if tolerated:
        names_listed = ", ".join(check["name"] for check, _ in tolerated)
        counts += f", red but also red on {base_ref} {len(tolerated)}: {names_listed}"
    notes.extend(warnings)
    return f"PASS {label} head={sha} checks={len(checks)} ({counts})"


# --------------------------------------------------------------------------
# modes


def gate_mode(bead_id: str, state: dict[str, Any]) -> str:
    bead = bead_show(bead_id)
    bead_meta = metadata(bead)
    root_id = bead_meta.get("gc.root_bead_id", "").strip() or bead_id
    root = bead if root_id == bead_id else bead_show(root_id)
    root_meta = metadata(root)
    state["root_id"] = root_id

    push, open_pr = root_meta.get("gc.var.push", ""), root_meta.get("gc.var.open_pr", "")
    may_push = is_true(push)
    intent = may_push and is_true(open_pr)
    intent_note = f"push={push or 'unset'} open_pr={open_pr or 'unset'}"

    commit = root_meta.get("gc.build.handoff_commit", "").strip()
    branch = root_meta.get("gc.build.handoff_branch", "").strip()
    worktree = ""
    source_id = root_meta.get("gc.drain_member_id", "").strip() or root_meta.get("gc.input_convoy_id", "").strip()
    if source_id:
        try:
            source_meta = metadata(bead_show(source_id))
        except GateFailure:
            source_meta = {}
        worktree = source_meta.get("work_dir", "").strip()
        # A source anchor that has already closed records what it shipped; its
        # worktree may be gone by then.
        commit = commit or source_meta.get("gc.work_commit", "").strip()
        branch = branch or source_meta.get("gc.work_branch", "").strip()
    if worktree and os.path.isdir(worktree):
        head = git(["rev-parse", "HEAD"], worktree)
        if head:
            if commit and not same_sha(commit, head):
                remedy = (
                    "Push the worktree head, wait for its checks, and record that commit"
                    if may_push
                    else "Record the commit that is actually handed over"
                )
                raise GateFailure(
                    f"gc.build.handoff_commit on {root_id} is {commit[:12]} but the implementation worktree {worktree} is at {head[:12]}. {remedy}"
                )
            commit = head
            branch = branch or git(["symbolic-ref", "--short", "-q", "HEAD"], worktree)

    if commit and not SHA_RE.match(commit):
        raise GateFailure(f"gc.build.handoff_commit on {root_id} is not a commit sha: {commit[:60]!r}")

    if not intent and not commit and not branch:
        raise Skip(f"no publishing intent ({intent_note}) and no handoff recorded on {root_id}")

    try:
        repo = resolve_repo([worktree, os.environ.get("GC_WORK_DIR", ""), os.environ.get("GC_STORE_PATH", ""), os.getcwd()])
        gh = GitHub()
    except Skip as skip:
        # With publishing intent this is a gate that should have run and did
        # not: say so where a failure would have been said.
        raise Skip(f"{skip} ({intent_note})", loud=intent) from None

    if intent and not commit:
        raise GateFailure(
            f"workflow root {root_id} intends to publish ({intent_note}) but no handoff commit is recorded. "
            f"Push the work branch, open a draft pull request, wait for its checks, then record gc.build.handoff_commit and gc.build.handoff_branch on {root_id}"
        )

    try:
        pull = find_pull(gh, repo, branch=branch, commit=commit)
    except Skip as skip:
        raise Skip(f"{skip} ({intent_note})", loud=intent or skip.loud) from None
    if pull is None:
        where = " or ".join(part for part in (f"branch {branch}" if branch else "", f"commit {commit[:12]}" if commit else "") if part)
        if not intent:
            raise Skip(f"no publishing intent ({intent_note}) and no open pull request for {where} in {repo}")
        if nothing_to_publish(gh, repo, commit):
            raise Skip(f"nothing to publish: handoff commit {commit[:12]} is already on the default branch of {repo} ({intent_note})")
        raise GateFailure(
            f"no open pull request for {where} in {repo}. Push the branch and open a draft pull request against the default branch "
            "(gh pr create --draft), wait for its checks, then close the step again"
        )
    head = str((pull.get("head") or {}).get("sha", ""))
    if not may_push and commit and head and not same_sha(head, commit):
        # The workflow was told not to push, so the handoff commit is local.
        # The pull request that exists is at another commit and its checks say
        # nothing about this one.
        raise Skip(
            f"{intent_note}: handoff commit {commit[:12]} is local only; open pull request {repo}#{pull.get('number')} "
            f"is at {head[:12]}, so GitHub has no checks for the commit handed to review",
            loud=True,
        )
    try:
        return check_pull(gh, repo, pull, commit, require_draft=True, may_push=may_push, notes=state.setdefault("notes", []))
    except Skip as skip:
        raise Skip(str(skip), loud=intent or skip.loud) from None


def nothing_to_publish(gh: GitHub, repo: str, commit: str) -> bool:
    """True when commit adds nothing to the default branch (a step that changed no code)."""
    try:
        default_branch = default_branch_of(gh, repo)
        if not default_branch:
            return False
        comparison = gh.api(f"repos/{repo}/compare/{quote_ref(default_branch)}...{commit}")
    except (NotOnGitHub, Refused):
        return False
    return isinstance(comparison, dict) and comparison.get("ahead_by") == 0


def default_branch_of(gh: GitHub, repo: str) -> str:
    info = gh.api(f"repos/{repo}")
    return str(info.get("default_branch") or "") if isinstance(info, dict) else ""


def check_pull(
    gh: GitHub, repo: str, pull: dict[str, Any], commit: str, *, require_draft: bool, may_push: bool = True, notes: list[str] | None = None
) -> str:
    number = pull.get("number")
    head = str((pull.get("head") or {}).get("sha", ""))
    label = f"{repo}#{number}"
    if not head:
        raise GateFailure(f"{label} has no head commit")
    if require_draft and pull.get("state") == "open" and not pull.get("draft"):
        raise GateFailure(
            f"{label} is marked ready for review but has not been reviewed, and a merge sweep merges any ready pull request "
            f"whose checks are green. Make it a draft again: gh pr ready --undo {number} --repo {repo}. "
            "Only the publish step marks it ready, after review approves"
        )
    if commit and not same_sha(head, commit):
        remedy = (
            "push the handoff commit, wait for its checks, and record the commit you pushed"
            if may_push
            else "record the commit that is actually on the pull request"
        )
        raise GateFailure(
            f"head mismatch: {label} head is {head[:12]} but the commit handed to review is {commit[:12]}. "
            f"Review must see the commit CI ran on: {remedy}"
        )
    base_ref = str((pull.get("base") or {}).get("ref") or "")
    result = evaluate(gh, repo, head, label, base_ref=base_ref, may_push=may_push, notes=notes)
    state = "draft" if pull.get("draft") else str(pull.get("state") or "")
    return f"{result} pr_state={state} url={pull.get('html_url', '')}"


def manual_mode(args: argparse.Namespace) -> str:
    repo = args.repo
    if not re.match(r"^[^/\s]+/[^/\s]+$", repo or ""):
        raise GateFailure("--repo OWNER/REPO is required with --pr or --commit")
    if args.commit and not SHA_RE.match(args.commit):
        raise GateFailure(f"--commit is not a commit sha: {args.commit[:60]!r}")
    gh = GitHub()
    commit = args.commit
    if commit and len(commit) < 40:
        # The workflow-runs listing only matches a full sha.
        try:
            found = gh.api(f"repos/{repo}/commits/{commit}")
        except NotOnGitHub:
            raise GateFailure(f"commit {commit[:12]} is not on GitHub in {repo}") from None
        commit = str(found.get("sha") or commit) if isinstance(found, dict) else commit

    if args.pr:
        pull = find_pull(gh, repo, number=str(args.pr))
        assert pull is not None
        if pull.get("state") != "open" and not args.any_state:
            raise GateFailure(f"{repo}#{pull.get('number')} is {pull.get('state')}, not open (use --any-state to inspect it anyway)")
    else:
        pull = find_pull(gh, repo, commit=commit, any_state=args.any_state, first_of_many=args.any_state)
        if pull is None and not args.any_state:
            raise GateFailure(f"no open pull request contains commit {commit[:12]} in {repo}")

    if pull is None:
        return evaluate(gh, repo, commit, f"{repo} commit", base_ref=default_branch_of(gh, repo))
    head = str((pull.get("head") or {}).get("sha", ""))
    if commit and args.any_state and not same_sha(head, commit):
        # Diagnostics on a commit that is not (or no longer) the head: an
        # older push, or the merge commit of a merged pull request.
        base_ref = str((pull.get("base") or {}).get("ref") or "")
        label = f"{repo}#{pull.get('number')} (commit given; the head is {head[:12]})"
        return evaluate(gh, repo, commit, label, base_ref=base_ref)
    return check_pull(gh, repo, pull, commit or "", require_draft=False)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pr-ci-green.sh",
        description="Pass only when the commit handed to review has complete, green GitHub checks.",
    )
    parser.add_argument("--repo", default="", help="OWNER/REPO (manual mode)")
    parser.add_argument("--pr", default="", help="Pull request number (manual mode)")
    parser.add_argument("--commit", default="", help="Commit sha to require as the pull request head (manual mode)")
    parser.add_argument(
        "--any-state",
        action="store_true",
        help="Manual mode: also inspect merged or closed pull requests, or a commit that is no longer the head",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    state: dict[str, Any] = {}
    try:
        if args.repo or args.pr or args.commit:
            if not (args.pr or args.commit):
                raise GateFailure("manual mode needs --pr or --commit")
            say(manual_mode(args))
        else:
            bead_id = os.environ.get("GC_BEAD_ID", "").strip()
            if not bead_id:
                raise GateFailure("GC_BEAD_ID is required (or use --repo with --pr/--commit)")
            result = gate_mode(bead_id, state)
            say(result)
            record_result(state.get("root_id", ""), " | ".join([result, *state.get("notes", [])]))
    except Skip as skip:
        say(f"skipped: {skip}")
        if skip.loud:
            warn(f"WARNING the CI gate did not run and the step passes unchecked: {skip}")
        record_result(state.get("root_id", ""), f"skipped: {skip}")
        return 0
    except NoVerdict as failure:
        warn(
            f"INFRA no verdict: {failure}. Nothing was learned about the commit; this is not a red check. "
            "Run the gate again when the service is back"
        )
        return EXIT_NO_VERDICT
    except GateFailure as failure:
        print(f"{PREFIX}: FAIL {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
