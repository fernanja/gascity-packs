#!/usr/bin/env bash
set -euo pipefail

# CI-green handoff gate (gc-68exu).
#
# A step that hands a commit to review may close only when that commit is the
# head of an open DRAFT pull request and GitHub's checks on it are complete and
# green: every check finished, the latest run of every workflow for the commit
# finished, every status check the base branch requires reported. A skipped
# check counts as green. A red check blocks, with two exceptions that pass
# with a WARNING: a GitHub Actions job the base branch does not require whose
# latest run on the base branch (same workflow, same job) is red at the same
# steps, and a check from another app that the base branch does not require.
# Same conventions as build-artifact-valid.sh: reads $GC_BEAD_ID, resolves the
# workflow root via gc.root_bead_id, prints failures on stderr for
# gc.attempt_log, never prompts. The rules are spelled out in pr_ci_green.py.
#
# Workflow-root metadata it reads:
#   gc.var.push, gc.var.open_pr   - both true means the workflow intends to
#                                   publish, so a pull request must exist
#   gc.build.handoff_commit       - the commit handed to review
#   gc.build.handoff_branch       - the branch it was pushed to
#   gc.drain_member_id            - for an implementation item, the source
#                                   anchor whose `work_dir` worktree head is the
#                                   handoff commit (no recording needed)
# It writes one key there, best effort: gc.build.ci_gate_result, the last
# PASS or skipped line.
#
# Exit 0: green, or an explicit `skipped: <reason>` (no publishing intent and
# no pull request, the remote is not GitHub, gh missing). Every skip is on
# stderr as well as stdout.
# Exit 1: red, unfinished, no pull request, not a draft, head mismatch.
# Exit 75: no verdict (GitHub or the bead store kept failing, or gh cannot
# authenticate in a workflow that intends to publish). Under the controller
# the script retries until the check timeout ends it instead, because only a
# timeout is not counted as a failed attempt.
#
# It never waits for CI: the worker does that before closing its step (a
# bounded `gh pr checks <n> --watch`; the step docs give the wrapper), because
# the controller runs checks one at a time and a 15-30 minute wait here would
# stall every other workflow in the rig.
#
# Manual, read-only use:
#   pr-ci-green.sh --repo OWNER/REPO --pr 123
#   pr-ci-green.sh --repo OWNER/REPO --pr 123 --commit <sha> --any-state
#   pr-ci-green.sh --repo OWNER/REPO --commit <sha> --any-state

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
command -v python3 >/dev/null 2>&1 || {
  echo "pr-ci-green: python3 is required on PATH" >&2
  exit 1
}

IMPL=""
for candidate in \
  "$SCRIPT_DIR/../pr_ci_green.py" \
  ${GC_WORK_DIR:+"$GC_WORK_DIR/gascity/assets/scripts/pr_ci_green.py"}; do
  if [ -n "$candidate" ] && [ -f "$candidate" ]; then
    IMPL="$candidate"
    break
  fi
done
[ -n "$IMPL" ] || {
  echo "pr-ci-green: pr_ci_green.py not found beside $SCRIPT_DIR" >&2
  exit 1
}

exec python3 "$IMPL" "$@"
