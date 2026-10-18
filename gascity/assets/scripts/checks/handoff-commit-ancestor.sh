#!/usr/bin/env bash
set -euo pipefail

# Hand-off gate (gc-gef2p8, reviewer-first flow; extended to the implementation
# steps by gc-jhuzd0).
#
# Used by the fix-pass apply step and, chained by implementation-handoff-valid.sh,
# by the implementation steps (do-work, do-work-item, implementation-base,
# implementation-item-base). In both, GC_BEAD_ID is the step and the keys are on
# its workflow root (gc.root_bead_id): the fix-loop root, or the item root.
# gc-jhuzd0: two of the first four reviewer-first items closed with a correct
# checkpoint on origin and no keys on the item root, so the summarize step
# bridged nothing. A publishing workflow now fails the close until both keys are
# recorded, and the failure names the two `gc bd update` calls to run.
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
# never prompts. It touches the network only in a workflow that publishes, for
# one bounded `git fetch origin <branch>` (and, when origin/HEAD is not set
# locally, one bounded `git ls-remote --symref origin HEAD`); it never calls gh.
#
# Workflow-root metadata it reads:
#   gc.build.handoff_commit    - the commit handed to the re-review
#   gc.build.handoff_branch    - the work branch that carries it
#   gc.var.push, gc.var.open_pr - both true means the workflow publishes, so a
#                                 recorded hand-off is required
#   gc.work_dir                - the launcher rig root; falls back to
#                                $GC_WORK_DIR, $GC_STORE_PATH, then $PWD
#
# When both gc.var.push and gc.var.open_pr are true, three more checks run
# after the ancestor check, each refusing with one line that names it:
#   skip-ci tag  - the commit message carries [skip ci], [ci skip], [no ci],
#                  [skip actions], [actions skip] or a `skip-checks: true`
#                  trailer, so the checkpoint push runs no CI
#   on origin    - refs/remotes/origin/<branch> (fetched now) contains the
#                  commit: "checkpoint not on origin"
#   not default  - the branch is not the repository default branch
#
# Exit 0: the commit is on the branch (PASS), or an explicit `skipped:` when
# the workflow does not publish and recorded nothing.
# Exit 1: nothing recorded in a workflow that publishes, the commit is not in
# the repository, the branch is missing, the commit is not on the branch, or
# (publishing workflows) the commit is untagged, not pushed, or on the default
# branch.
# Exit 75: no verdict (the bead store kept failing); not a failed attempt.
#
# Manual use: GC_BEAD_ID=<fix-pass step id> handoff-commit-ancestor.sh

PREFIX="handoff-commit-ancestor"

say() { printf '%s: %s\n' "$PREFIX" "$*"; }
fail() { printf '%s: FAIL %s\n' "$PREFIX" "$*" >&2; exit 1; }
infra() { printf '%s: INFRA no verdict: %s. Nothing was learned about the commit; this is not a failed hand-off. Run the gate again when the bead store is back\n' "$PREFIX" "$*" >&2; exit 75; }

# run_bounded <command>...: run with an alarm (perl is on macOS and Linux; GNU
# timeout is not on macOS). Never prompts for credentials.
run_bounded() {
  if command -v perl >/dev/null 2>&1; then
    GIT_TERMINAL_PROMPT=0 perl -e 'alarm shift; exec @ARGV or exit 127' "${HANDOFF_ANCESTOR_NETWORK_TIMEOUT_SECONDS:-60}" "$@"
  else
    GIT_TERMINAL_PROMPT=0 "$@"
  fi
}

# record_calls <root-id> <commit> <branch>: the two calls that record the
# hand-off, one per key, as one line for a failure message.
record_calls() {
  printf 'gc bd update "%s" --set-metadata "gc.build.handoff_commit=%s" ; gc bd update "%s" --set-metadata "gc.build.handoff_branch=%s"' "$1" "$2" "$1" "$3"
}

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
    fail "no handoff commit is recorded: workflow root $ROOT_ID publishes (push=$PUSH open_pr=$OPEN_PR) but carries no gc.build.handoff_commit. Commit the work, make the one checkpoint push, then run both calls and close the step again: $(record_calls "$ROOT_ID" '$(git rev-parse HEAD)' '<work-branch>')"
  fi
  say "skipped: no publishing intent (push=${PUSH:-unset} open_pr=${OPEN_PR:-unset}) and no handoff recorded on $ROOT_ID"
  exit 0
fi

printf '%s' "$COMMIT" | grep -Eq '^[0-9a-fA-F]{7,64}$' || fail "gc.build.handoff_commit on $ROOT_ID is not a commit sha: '${COMMIT:0:60}'"
[ -n "$BRANCH" ] || fail "gc.build.handoff_branch is not recorded on $ROOT_ID. Record the work branch that carries ${COMMIT:0:12}: $(record_calls "$ROOT_ID" "$COMMIT" '<work-branch>')"

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

if [ "$PUSH" = "true" ] && [ "$OPEN_PR" = "true" ]; then
  # (a) The checkpoint must not run CI: only publish does.
  COMMIT_MESSAGE="$(git -C "$REPO" log -1 --format=%B "$COMMIT")"
  if ! printf '%s\n' "$COMMIT_MESSAGE" | grep -Eq '\[(skip ci|ci skip|no ci|skip actions|actions skip)\]|^skip-checks: true[[:space:]]*$'; then
    fail "head commit carries no skip-ci tag: ${COMMIT:0:12} on $BRANCH would run CI at the checkpoint push. Amend its message to carry [skip ci] (or a 'skip-checks: true' trailer), push again and re-record gc.build.handoff_commit"
  fi

  # (c) Never the default branch: a checkpoint there is a direct push to main.
  DEFAULT_BRANCH="$(git -C "$REPO" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  DEFAULT_BRANCH="${DEFAULT_BRANCH#origin/}"
  if [ -z "$DEFAULT_BRANCH" ]; then
    DEFAULT_BRANCH="$(run_bounded git -C "$REPO" ls-remote --symref origin HEAD 2>/dev/null | sed -n 's|^ref: refs/heads/\([^[:space:]]*\)[[:space:]]*HEAD$|\1|p' | head -n 1 || true)"
  fi
  if [ "$BRANCH" = "$DEFAULT_BRANCH" ] || { [ -z "$DEFAULT_BRANCH" ] && { [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; }; }; then
    fail "work branch '$BRANCH' is the default branch of $REPO; a checkpoint push there bypasses the pull request. Hand off from a work branch"
  fi

  # (b) The checkpoint push must have happened. Fetch it fresh: the local
  # remote-tracking ref says what this clone last saw, not what origin holds.
  # The controller runs this gate without GitHub credentials (gc-gef2p8 follow-up, 2026-10-18):
  # the fetch is a best-effort refresh. The worker's checkpoint push already updated the
  # shared repository's remote-tracking ref, so fall back to it when the fetch cannot run.
  if ! FETCH_OUT="$(run_bounded git -C "$REPO" fetch --quiet origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" 2>&1)"; then
    if git -C "$REPO" rev-parse --verify --quiet "refs/remotes/origin/$BRANCH" >/dev/null 2>&1; then
      echo "handoff-commit-ancestor: fetch of origin/$BRANCH failed ($(printf '%s' "$FETCH_OUT" | tr '\n' ' ' | head -c 120)); using the existing remote-tracking ref" >&2
    else
      fail "checkpoint not on origin: git fetch origin $BRANCH failed ($(printf '%s' "$FETCH_OUT" | tr '\n' ' ' | head -c 160)) and no remote-tracking ref exists. Push $BRANCH once (fast-forward) before closing"
    fi
  fi
  if ! git -C "$REPO" merge-base --is-ancestor "$COMMIT" "refs/remotes/origin/$BRANCH"; then
    fail "checkpoint not on origin: ${COMMIT:0:12} is not in origin/$BRANCH. Make the one checkpoint push (git push origin $BRANCH) before closing"
  fi
fi

say "PASS handoff commit ${COMMIT:0:12} is on $BRANCH (head ${HEAD_SHA:0:12}); CI deferred to publish"
