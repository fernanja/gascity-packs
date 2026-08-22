#!/usr/bin/env bash
set -euo pipefail

# Mechanical local-preflight gate.
#
# Companion to build-artifact-valid.sh, same conventions (GC_BEAD_ID ->
# workflow root -> metadata lookup, fail() prints machine-readable stderr
# lines the dispatcher records in gc.attempt_log for the next bounded
# attempt). Where build-artifact-valid.sh checks that a report/summary
# ARTIFACT has the right shape, this checks that the CODE it describes
# actually passes the rig's full local-CI-equivalent gate -- independently
# re-run here, not trusted from the agent's own prose claim in the summary.
# This is what makes the preflight requirement mechanical instead of an
# instruction a reviewer can miss (see review/write-report.md's prose
# version, which a re-review pass confirmed is attention-dependent, not
# guaranteed).
#
# Resolves the worktree(s) under review from the workflow root's recorded
# review-subject metadata (whichever of the shapes below is present; a
# multi-item convoy review records one anchor per item, a single-item
# review records one subject path/dir), cds into each, and runs
# `make preflight-fast` (falling back to `make preflight` if the rig's
# Makefile has no preflight-fast target). Any failing anchor fails the
# whole check.
#
# Chains to build-artifact-valid.sh first (same $GC_BEAD_ID, same step) so a
# step that needs both checks (e.g. review.write-report) can still point
# [steps.check.check].path at a single script -- the artifact-schema gate
# stays exactly as strict as it already was, this just adds the code-level
# gate after it passes.

fail() {
  echo "preflight-evidence-check: $*" >&2
  exit 1
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$SCRIPT_DIR/build-artifact-valid.sh" ]; then
  "$SCRIPT_DIR/build-artifact-valid.sh" || exit 1
fi

BEAD_ID="${GC_BEAD_ID:-}"
[ -n "$BEAD_ID" ] || fail "GC_BEAD_ID is required"
command -v bd >/dev/null 2>&1 || fail "bd is required on PATH"
command -v python3 >/dev/null 2>&1 || fail "python3 is required on PATH"
command -v make >/dev/null 2>&1 || fail "make is required on PATH"

metadata_value() {
  # metadata_value <json> <key> -> prints metadata[key] or empty
  printf '%s' "$1" | python3 -c '
import json
import sys

key = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
if isinstance(data, list):
    data = data[0] if data else {}
if not isinstance(data, dict):
    print("")
    raise SystemExit(0)
metadata = data.get("metadata") or {}
value = metadata.get(key, "") if isinstance(metadata, dict) else ""
print(value if isinstance(value, str) else "")
' "$2"
}

SHOW_JSON="$(bd show "$BEAD_ID" --json 2>/dev/null)" || fail "bd show $BEAD_ID failed"

ROOT_ID="$(metadata_value "$SHOW_JSON" "gc.root_bead_id")"
ROOT_JSON="$SHOW_JSON"
if [ -n "$ROOT_ID" ] && [ "$ROOT_ID" != "$BEAD_ID" ]; then
  ROOT_JSON="$(bd show "$ROOT_ID" --json 2>/dev/null)" || fail "bd show $ROOT_ID failed"
fi
[ -n "$ROOT_ID" ] || ROOT_ID="$BEAD_ID"

# Resolve one or more worktree directories to check. Tries, in order:
#   1. gc.build.review_subject.v1 = {"anchors":[{"worktree": "...", ...}, ...]}
#      (multi-item convoy review -- one anchor per implementation item)
#   2. gc.build.review_subject_path pointing at a file inside the worktree
#      (single-item review -- take the worktree as that file's directory,
#      walked up to the nearest git root)
WORKTREES_JSON="$(metadata_value "$ROOT_JSON" "gc.build.review_subject.v1")"
WORKTREES="$(printf '%s' "$WORKTREES_JSON" | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit(0)
try:
    data = json.loads(raw)
except Exception:
    raise SystemExit(0)
for anchor in data.get("anchors", []):
    wt = anchor.get("worktree")
    if wt:
        print(wt)
' 2>/dev/null)"

if [ -z "$WORKTREES" ]; then
  SUBJECT_PATH="$(metadata_value "$ROOT_JSON" "gc.build.review_subject_path")"
  if [ -n "$SUBJECT_PATH" ]; then
    SUBJECT_DIR="$(dirname "$SUBJECT_PATH")"
    WT="$(cd "$SUBJECT_DIR" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)" || WT=""
    [ -n "$WT" ] && WORKTREES="$WT"
  fi
fi

[ -n "$WORKTREES" ] || fail "no worktree could be resolved from gc.build.review_subject.v1 or gc.build.review_subject_path on workflow root $ROOT_ID -- the producing/prepare-review stage must record one before this check can run"

FAILED=0
CHECKED=0
while IFS= read -r WT; do
  [ -n "$WT" ] || continue
  CHECKED=$((CHECKED + 1))
  [ -d "$WT" ] || { echo "preflight-evidence-check: worktree $WT does not exist (may have been cleaned up)" >&2; FAILED=1; continue; }

  TARGET="preflight-fast"
  if ! (cd "$WT" && grep -q '^preflight-fast:' Makefile 2>/dev/null); then
    TARGET="preflight"
  fi

  LOG="$(mktemp)"
  if (cd "$WT" && [ "$(pwd -P)" = "$(cd "$WT" && pwd -P)" ] && make "$TARGET" >"$LOG" 2>&1); then
    echo "preflight-evidence-check: PASS worktree=$WT target=$TARGET"
  else
    echo "preflight-evidence-check: FAIL worktree=$WT target=$TARGET -- tail of output:" >&2
    tail -n 40 "$LOG" >&2
    FAILED=1
  fi
  rm -f "$LOG"
done <<<"$WORKTREES"

[ "$CHECKED" -gt 0 ] || fail "resolved worktree list was empty after filtering"
[ "$FAILED" -eq 0 ] || fail "one or more worktrees failed local preflight -- fix the code (not this report) before re-attempting; see the FAIL lines above for which target and worktree"

echo "preflight-evidence-check: PASS ($CHECKED worktree(s) checked)"
exit 0
