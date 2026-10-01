#!/usr/bin/env bash
set -euo pipefail

# CI-green handoff gate (gc-68exu).
#
# A step that hands a commit to review may close only when that commit is the
# head of an open pull request and every GitHub check on it is complete and
# green (a skipped check counts as green). Same conventions as
# build-artifact-valid.sh: reads $GC_BEAD_ID, resolves the workflow root via
# gc.root_bead_id, prints failures on stderr for gc.attempt_log, never prompts.
#
# Workflow-root metadata it reads:
#   gc.var.push, gc.var.open_pr   - both true means the workflow intends to
#                                   publish, so a pull request must exist
#   gc.build.handoff_commit       - the commit handed to review
#   gc.build.handoff_branch       - the branch it was pushed to
#   gc.drain_member_id            - for an implementation item, the source
#                                   anchor whose `work_dir` worktree head is the
#                                   handoff commit (no recording needed)
#
# It passes with an explicit `skipped: <reason>` line when the workflow has no
# publishing intent and no pull request, the remote is not GitHub, or gh is
# missing or signed out. It never waits for CI: the worker does that before
# closing its step (`gh pr checks <n> --watch`), because the controller runs
# checks one at a time and a 15-30 minute wait here would stall every other
# workflow in the rig.
#
# Manual, read-only use:
#   pr-ci-green.sh --repo OWNER/REPO --pr 123
#   pr-ci-green.sh --repo OWNER/REPO --pr 123 --commit <sha> --any-state

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
