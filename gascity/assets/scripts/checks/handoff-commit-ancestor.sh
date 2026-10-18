#!/usr/bin/env bash
set -euo pipefail

# Fix-pass hand-off gate (gc-gef2p8, reviewer-first flow).
#
# A fix pass hands its work to the re-review as a local commit. It closes only
# when the commit it recorded as gc.build.handoff_commit exists in the rig
# repository and is an ancestor of (or equal to) the head of the work branch
# it recorded as gc.build.handoff_branch. There is no CI to wait for: the
# re-review works from that commit, and the one CI run happens at publish.
# This replaces the pr-ci-green.sh gate on the apply step.
#
# Same conventions as pr-ci-green.sh: reads $GC_BEAD_ID, resolves the workflow
# root through gc.root_bead_id, prints failures on stderr for gc.attempt_log,
# never prompts, never touches the network.
#
# Workflow-root metadata it reads:
#   gc.build.handoff_commit    - the commit handed to the re-review
#   gc.build.handoff_branch    - the work branch that carries it
#   gc.var.push, gc.var.open_pr - both true means the workflow publishes, so a
#                                 recorded hand-off is required
#   gc.work_dir                - the launcher rig root; falls back to
#                                $GC_WORK_DIR, $GC_STORE_PATH, then $PWD
#
# Exit 0: the commit is on the branch (PASS), or an explicit `skipped:` when
# the workflow does not publish and recorded nothing.
# Exit 1: nothing recorded in a workflow that publishes, the commit is not in
# the repository, the branch is missing, or the commit is not on the branch.
# Exit 75: no verdict (the bead store kept failing); not a failed attempt.
#
# Manual use: GC_BEAD_ID=<fix-pass step id> handoff-commit-ancestor.sh

PREFIX="handoff-commit-ancestor"

say() { printf '%s: %s\n' "$PREFIX" "$*"; }
fail() { printf '%s: FAIL %s\n' "$PREFIX" "$*" >&2; exit 1; }
infra() { printf '%s: INFRA no verdict: %s. Nothing was learned about the commit; this is not a failed hand-off. Run the gate again when the bead store is back\n' "$PREFIX" "$*" >&2; exit 75; }

command -v python3 >/dev/null 2>&1 || fail "python3 is required on PATH"
command -v git >/dev/null 2>&1 || fail "git is required on PATH"
[ -n "${GC_BEAD_ID:-}" ] || fail "GC_BEAD_ID is required"

# bead_meta <bead-id> <key>...: print one value per line, empty when unset.
bead_meta() {
  local id="$1" out="" attempt
  shift
  for attempt in 1 2 3; do
    if out="$(gc bd show "$id" --json 2>&1)"; then
      printf '%s' "$out" | python3 -c '
import json, sys
data = json.load(sys.stdin)
bead = data[0] if isinstance(data, list) else data
meta = bead.get("metadata") or {}
for key in sys.argv[1:]:
    value = meta.get(key, "")
    print(str(value).strip().replace("\n", " "))
' "$@" && return 0
    fi
    sleep "${HANDOFF_ANCESTOR_RETRY_SLEEP_SECONDS:-2}"
  done
  infra "gc bd show $id failed: $(printf '%s' "$out" | head -c 200)"
}

# Assigning from a command substitution keeps `infra`'s exit 75 (a process
# substitution would swallow it).
ROOT_ID="$(bead_meta "$GC_BEAD_ID" gc.root_bead_id)"
[ -n "$ROOT_ID" ] || ROOT_ID="$GC_BEAD_ID"
ROOT_VALUES="$(bead_meta "$ROOT_ID" gc.build.handoff_commit gc.build.handoff_branch gc.var.push gc.var.open_pr gc.work_dir)"
COMMIT="" BRANCH="" PUSH="" OPEN_PR="" WORK_DIR=""
{
  IFS= read -r COMMIT || true
  IFS= read -r BRANCH || true
  IFS= read -r PUSH || true
  IFS= read -r OPEN_PR || true
  IFS= read -r WORK_DIR || true
} <<<"$ROOT_VALUES"

if [ -z "$COMMIT" ]; then
  if [ "$PUSH" = "true" ] && [ "$OPEN_PR" = "true" ]; then
    fail "workflow root $ROOT_ID publishes (push=$PUSH open_pr=$OPEN_PR) but no handoff commit is recorded. Commit the fix, then record gc.build.handoff_commit and gc.build.handoff_branch on $ROOT_ID"
  fi
  say "skipped: no publishing intent (push=${PUSH:-unset} open_pr=${OPEN_PR:-unset}) and no handoff recorded on $ROOT_ID"
  exit 0
fi

printf '%s' "$COMMIT" | grep -Eq '^[0-9a-fA-F]{7,64}$' || fail "gc.build.handoff_commit on $ROOT_ID is not a commit sha: '${COMMIT:0:60}'"
[ -n "$BRANCH" ] || fail "gc.build.handoff_branch is not recorded on $ROOT_ID. Record the work branch that carries $COMMIT"

REPO=""
for candidate in "$WORK_DIR" "${GC_WORK_DIR:-}" "${GC_STORE_PATH:-}" "$PWD"; do
  if [ -n "$candidate" ] && git -C "$candidate" rev-parse --git-dir >/dev/null 2>&1; then
    REPO="$candidate"
    break
  fi
done
[ -n "$REPO" ] || fail "no git repository found for the rig (gc.work_dir='${WORK_DIR}')"

git -C "$REPO" cat-file -e "${COMMIT}^{commit}" 2>/dev/null \
  || fail "handoff commit ${COMMIT:0:12} does not exist in $REPO. Commit in the work branch's worktree, which shares this repository's object store, and record the sha you committed"

HEAD_SHA=""
for ref in "refs/heads/$BRANCH" "refs/remotes/origin/$BRANCH"; do
  if HEAD_SHA="$(git -C "$REPO" rev-parse --verify --quiet "${ref}^{commit}")"; then
    break
  fi
  HEAD_SHA=""
done
[ -n "$HEAD_SHA" ] || fail "work branch '$BRANCH' (gc.build.handoff_branch on $ROOT_ID) does not exist in $REPO, locally or on origin"

if ! git -C "$REPO" merge-base --is-ancestor "$COMMIT" "$HEAD_SHA"; then
  fail "handoff commit ${COMMIT:0:12} is not an ancestor of the head of $BRANCH (${HEAD_SHA:0:12}). Record a commit that is on the work branch, or put the commit on it"
fi

say "PASS handoff commit ${COMMIT:0:12} is on $BRANCH (head ${HEAD_SHA:0:12}); CI deferred to publish"
