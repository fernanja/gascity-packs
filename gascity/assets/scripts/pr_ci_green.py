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

The answer is deterministic for a given commit: every check run and commit
status on it must be complete, and each must have concluded success, skipped
or neutral. Anything else is red, including a cancelled or timed-out job: this
script does not classify flakes. A check that has not finished is not green.

The script only reads. It never waits: the worker waits for CI (for example
`gh pr checks <n> --watch`) before closing its step, and this confirms the
result in a few API calls, because the controller that runs step checks
processes one control bead at a time and must not be held for a CI run.

Exit 0: green, or an explicit `skipped: <reason>` (no publishing intent, no
GitHub remote, gh missing or not signed in, repository without CI).
Exit 1: red, pending, no pull request, head mismatch, or GitHub unreachable.
Failure detail goes to stderr, which the dispatcher records in gc.attempt_log
for the next attempt.
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
from typing import Any

PREFIX = "pr-ci-green"
GREEN_CONCLUSIONS = {"success", "skipped", "neutral"}
MAX_LISTED = 25
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
GH_ATTEMPTS = 3
try:
    GH_RETRY_SLEEP_SECONDS = float(os.environ.get("PR_CI_GREEN_RETRY_SLEEP_SECONDS", "2"))
except ValueError:
    GH_RETRY_SLEEP_SECONDS = 2.0
GC_CALL_TIMEOUT_SECONDS = 120
GITHUB_REMOTE_RE = re.compile(
    r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$"
)
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


class GateFailure(Exception):
    """The gate ran and the answer is no."""


class NotOnGitHub(GateFailure):
    """GitHub does not know the object asked for (HTTP 404 or 422)."""


class Skip(Exception):
    """The gate does not apply; the reason is recorded and the step passes."""


def say(message: str) -> None:
    print(f"{PREFIX}: {message}")


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


# --------------------------------------------------------------------------
# beads


def bead_show(bead_id: str) -> dict[str, Any]:
    if shutil.which("gc") is None:
        raise GateFailure("gc is required on PATH to read the workflow beads")
    try:
        proc = run(["gc", "bd", "show", bead_id, "--json"], timeout=GC_CALL_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateFailure(f"gc bd show {bead_id} failed: {exc}") from exc
    if proc.returncode != 0:
        raise GateFailure(f"gc bd show {bead_id} failed: {proc.stderr.strip()[:300]}")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise GateFailure(f"gc bd show {bead_id} did not return JSON") from exc
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise GateFailure(f"gc bd show {bead_id} returned an unexpected shape")
    return data


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
        raise Skip(f"origin is not a github.com remote ({redact_url(seen_remote)})")
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
        last = ""
        for attempt in range(GH_ATTEMPTS):
            try:
                proc = run([self.binary, "api", path], env=self.env, timeout=GH_CALL_TIMEOUT_SECONDS)
            except (OSError, subprocess.SubprocessError) as exc:
                last = str(exc)
            else:
                if proc.returncode == 0:
                    try:
                        return json.loads(proc.stdout)
                    except ValueError:
                        last = "response was not JSON"
                else:
                    last = (proc.stderr.strip() or proc.stdout.strip())[:300]
                    if "HTTP 404" in last or "HTTP 422" in last:
                        raise NotOnGitHub(f"GitHub does not have {path}: {last}")
                    if "HTTP 401" in last or "HTTP 403" in last:
                        break
            if attempt + 1 < GH_ATTEMPTS:
                time.sleep(GH_RETRY_SLEEP_SECONDS)
        raise GateFailure(f"GitHub could not be read ({path}): {last}. The result is unknown, so the gate does not pass")


# --------------------------------------------------------------------------
# evaluation


def find_pull(gh: GitHub, repo: str, *, number: str, branch: str, commit: str, any_state: bool) -> dict[str, Any] | None:
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
    elif commit:
        try:
            found = gh.api(f"repos/{repo}/commits/{commit}/pulls?per_page=100")
        except NotOnGitHub:
            return None  # the commit was never pushed, so no pull request carries it
        pulls = [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []
        if not any_state:
            pulls = [item for item in pulls if item.get("state") == "open"]
    if not pulls:
        return None
    if len(pulls) > 1:
        exact = [item for item in pulls if commit and same_sha(str((item.get("head") or {}).get("sha", "")), commit)]
        if len(exact) == 1:
            return exact[0]
        listed = ", ".join(f"#{item.get('number')} {item.get('html_url', '')}" for item in pulls[:5])
        raise GateFailure(f"more than one pull request matches the handoff in {repo}: {listed}. Close the extra one so one pull request carries this work")
    return pulls[0]


def same_sha(left: str, right: str) -> bool:
    left, right = left.strip().lower(), right.strip().lower()
    if not left or not right:
        return False
    return left.startswith(right) or right.startswith(left)


def collect_checks(gh: GitHub, repo: str, sha: str) -> list[dict[str, str]]:
    """Every check run and commit status on sha, as {name, state, detail, url}.

    state is one of ok, skipped, failed, pending.
    """
    checks: list[dict[str, str]] = []
    for page in range(1, 11):
        try:
            data = gh.api(f"repos/{repo}/commits/{sha}/check-runs?filter=latest&per_page=100&page={page}")
        except NotOnGitHub:
            raise GateFailure(f"commit {sha[:12]} is not on GitHub in {repo}: push it before handing it to review") from None
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


def evaluate(gh: GitHub, repo: str, sha: str, label: str) -> str:
    checks = collect_checks(gh, repo, sha)
    if not checks:
        workflows = gh.api(f"repos/{repo}/actions/workflows?per_page=1")
        if isinstance(workflows, dict) and workflows.get("total_count") == 0:
            raise Skip(f"{repo} has no workflows and no check has reported on {sha[:12]} ({label})")
        raise GateFailure(
            f"no check has reported on {sha[:12]} ({label}) yet. CI has not started or has not registered: "
            "wait for the checks to appear and finish, then close the step again"
        )
    failed = [check for check in checks if check["state"] == "failed"]
    pending = [check for check in checks if check["state"] == "pending"]
    if failed or pending:
        lines = [f"{label} head {sha[:12]}: {len(failed)} failed, {len(pending)} unfinished, of {len(checks)} checks"]
        listed = [("FAILED ", check) for check in failed] + [("PENDING", check) for check in pending]
        for kind, check in listed[:MAX_LISTED]:
            lines.append(f"  {kind} {check['name']} ({check['detail']}) {check['url']}".rstrip())
        if len(listed) > MAX_LISTED:
            lines.append(f"  ... and {len(listed) - MAX_LISTED} more")
        if failed:
            lines.append("Read each failed job's log, fix the cause on this branch, push, and wait for the new run. A failure in infrastructure is still a failed check.")
        else:
            lines.append("Wait for the unfinished checks to complete, then close the step again.")
        raise GateFailure("\n".join(lines))
    skipped = sum(1 for check in checks if check["state"] == "skipped")
    return f"PASS {label} head={sha} checks={len(checks)} (passed {len(checks) - skipped}, skipped {skipped})"


# --------------------------------------------------------------------------
# modes


def gate_mode(bead_id: str) -> str:
    bead = bead_show(bead_id)
    bead_meta = metadata(bead)
    root_id = bead_meta.get("gc.root_bead_id", "").strip() or bead_id
    root = bead if root_id == bead_id else bead_show(root_id)
    root_meta = metadata(root)

    push, open_pr = root_meta.get("gc.var.push", ""), root_meta.get("gc.var.open_pr", "")
    intent = is_true(push) and is_true(open_pr)
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
                raise GateFailure(
                    f"gc.build.handoff_commit on {root_id} is {commit[:12]} but the implementation worktree {worktree} is at {head[:12]}. "
                    "Push the worktree head, wait for its checks, and record that commit"
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
        raise Skip(f"{skip} ({intent_note})") from None

    if intent and not commit:
        raise GateFailure(
            f"workflow root {root_id} intends to publish ({intent_note}) but no handoff commit is recorded. "
            f"Push the work branch, open a draft pull request, wait for its checks, then record gc.build.handoff_commit and gc.build.handoff_branch on {root_id}"
        )

    pull = find_pull(gh, repo, number="", branch=branch, commit=commit, any_state=False)
    if pull is None:
        where = f"branch {branch}" if branch else f"commit {commit[:12]}"
        if not intent:
            raise Skip(f"no publishing intent ({intent_note}) and no open pull request for {where} in {repo}")
        if nothing_to_publish(gh, repo, commit):
            raise Skip(f"nothing to publish: handoff commit {commit[:12]} is already on the default branch of {repo} ({intent_note})")
        raise GateFailure(
            f"no open pull request for {where} in {repo}. Push the branch and open a draft pull request against the default branch "
            "(gh pr create --draft), wait for its checks, then close the step again"
        )
    return check_pull(gh, repo, pull, commit)


def nothing_to_publish(gh: GitHub, repo: str, commit: str) -> bool:
    """True when commit adds nothing to the default branch (a step that changed no code)."""
    try:
        info = gh.api(f"repos/{repo}")
        default_branch = str(info.get("default_branch") or "") if isinstance(info, dict) else ""
        if not default_branch:
            return False
        comparison = gh.api(f"repos/{repo}/compare/{default_branch}...{commit}")
    except NotOnGitHub:
        return False
    return isinstance(comparison, dict) and comparison.get("ahead_by") == 0


def check_pull(gh: GitHub, repo: str, pull: dict[str, Any], commit: str) -> str:
    number = pull.get("number")
    head = str((pull.get("head") or {}).get("sha", ""))
    label = f"{repo}#{number}"
    if not head:
        raise GateFailure(f"{label} has no head commit")
    if commit and not same_sha(head, commit):
        raise GateFailure(
            f"head mismatch: {label} head is {head[:12]} but the commit handed to review is {commit[:12]}. "
            "Review must see the commit CI ran on: push the handoff commit, wait for its checks, and record the commit you pushed"
        )
    result = evaluate(gh, repo, head, label)
    state = "draft" if pull.get("draft") else str(pull.get("state") or "")
    return f"{result} pr_state={state} url={pull.get('html_url', '')}"


def manual_mode(args: argparse.Namespace) -> str:
    repo = args.repo
    if not re.match(r"^[^/\s]+/[^/\s]+$", repo or ""):
        raise GateFailure("--repo OWNER/REPO is required with --pr or --commit")
    if args.commit and not SHA_RE.match(args.commit):
        raise GateFailure(f"--commit is not a commit sha: {args.commit[:60]!r}")
    gh = GitHub()
    if args.pr:
        pull = find_pull(gh, repo, number=str(args.pr), branch="", commit="", any_state=True)
        assert pull is not None
        if args.commit and args.any_state:
            # Diagnostics on an older head of this pull request.
            return evaluate(gh, repo, args.commit, f"{repo}#{pull.get('number')} (commit given)")
        if pull.get("state") != "open" and not args.any_state:
            raise GateFailure(f"{repo}#{pull.get('number')} is {pull.get('state')}, not open (use --any-state to inspect it anyway)")
        return check_pull(gh, repo, pull, args.commit or "")
    pull = find_pull(gh, repo, number="", branch="", commit=args.commit, any_state=args.any_state)
    if pull is None:
        if args.any_state:
            return evaluate(gh, repo, args.commit, f"{repo} commit")
        raise GateFailure(f"no open pull request contains commit {args.commit[:12]} in {repo}")
    return check_pull(gh, repo, pull, args.commit)


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
    try:
        if args.repo or args.pr or args.commit:
            if not (args.pr or args.commit):
                raise GateFailure("manual mode needs --pr or --commit")
            say(manual_mode(args))
        else:
            bead_id = os.environ.get("GC_BEAD_ID", "").strip()
            if not bead_id:
                raise GateFailure("GC_BEAD_ID is required (or use --repo with --pr/--commit)")
            say(gate_mode(bead_id))
    except Skip as skip:
        say(f"skipped: {skip}")
        return 0
    except GateFailure as failure:
        print(f"{PREFIX}: FAIL {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
