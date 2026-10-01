#!/usr/bin/env bash
set -euo pipefail

# Generic producer-stage build-artifact validation gate.
#
# The checked formula step names its artifact contract in step metadata:
#   gc.build.artifact_schema    - expected schema id (e.g. gc.build.requirements.v1)
#   gc.build.artifact_path_keys - comma-separated workflow-root metadata keys;
#                                 the first non-empty value is the artifact path
#
# The step bead (and the ralph control bead cloned from it) carries that
# metadata, so this script reads $GC_BEAD_ID, resolves the workflow root via
# gc.root_bead_id, resolves the artifact path, and validates the artifact with
# the shared base validator. All failures print machine-readable lines on
# stderr; the dispatcher records them in gc.attempt_log as repair context for
# the next bounded producer attempt. This gate never prompts.
#
# Optional step metadata:
#   gc.build.coverage_permits=required - the artifact must have a coverage entry
#     for every requirement id the requirements artifact defines, and may leave
#     one at a status other than "covered" only with a `permit` quoting
#     requirements text that hands it off (gc-gdyaz). The requirements artifact
#     is the one the workflow records (gc.build.requirements_path, then
#     gc.var.requirements_path), never one the artifact names for itself. It is
#     looked for on the workflow root and then, for an implementation item, on
#     the workflow that launched it (gc.drain_control_id -> that bead's
#     gc.root_bead_id): build-basic and build-from-requirements record the path
#     their own requirements stage wrote on the parent root only. If no root
#     records one, a quote is never checked against some other text: an
#     artifact that needs a permit fails with how to record the path.
#
# Exit codes: 0 valid; 1 invalid (a failed attempt); 75 no verdict, because
# `gc bd show` kept failing for a reason that says nothing about the artifact.
# The dispatcher counts any exit code as a failed attempt and only a check
# still running at its timeout as "could not run", so under the controller
# (GC_ITERATION is set) the retries outlast the "5m" check timeout.

fail() {
  echo "build-artifact-check: $*" >&2
  exit 1
}

BEAD_ID="${GC_BEAD_ID:-}"
[ -n "$BEAD_ID" ] || fail "GC_BEAD_ID is required"
command -v gc >/dev/null 2>&1 || fail "gc is required on PATH"
command -v python3 >/dev/null 2>&1 || fail "python3 is required on PATH"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

EXIT_NO_VERDICT=75
INFRA_BUDGET_SECONDS="${BUILD_ARTIFACT_INFRA_BUDGET_SECONDS:-}"
if [ -z "$INFRA_BUDGET_SECONDS" ]; then
  if [ -n "${GC_ITERATION:-}" ]; then INFRA_BUDGET_SECONDS=330; else INFRA_BUDGET_SECONDS=45; fi
fi
RETRY_SLEEP_SECONDS="${BUILD_ARTIFACT_RETRY_SLEEP_SECONDS:-2}"

WORK_TMP="$(mktemp -d)"
cleanup() {
  rm -rf "$WORK_TMP"
}
trap cleanup EXIT

bd_show() {
  # bd_show <bead-id> -> prints the bead JSON.
  # Returns 1 when the bead does not exist, $EXIT_NO_VERDICT when the store
  # kept failing (lock timeout, circuit breaker, a server restart).
  local id="$1" out err delay="$RETRY_SLEEP_SECONDS" announced=""
  while :; do
    if out="$(gc bd show "$id" --json 2>"$WORK_TMP/bd-show.err")"; then
      printf '%s' "$out"
      return 0
    fi
    err="$(head -c 300 "$WORK_TMP/bd-show.err" | tr '\n' ' ')"
    if printf '%s %s' "$out" "$err" | grep -Eqi 'no issues? found'; then
      echo "build-artifact-check: bead $id does not exist: $err" >&2
      return 1
    fi
    if [ "$SECONDS" -ge "$INFRA_BUDGET_SECONDS" ]; then
      echo "build-artifact-check: INFRA no verdict: gc bd show $id kept failing: ${err:-no output}. Nothing was learned about the artifact; this is not a validation failure" >&2
      return "$EXIT_NO_VERDICT"
    fi
    if [ -z "$announced" ]; then
      # Said now, not at the end: under the controller the check timeout ends
      # this process before it could say anything later.
      echo "build-artifact-check: INFRA gc bd show $id failed: ${err:-no output}. This says nothing about the artifact, so there is no verdict yet; retrying with backoff. If it is still failing when the check times out, the controller runs the check again without counting a failed attempt" >&2
      announced=1
    fi
    sleep "$delay"
    case "$delay" in
      *.*) ;;
      *) delay=$((delay * 2)); [ "$delay" -le 30 ] || delay=30 ;;
    esac
  done
}

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

SHOW_JSON="$(bd_show "$BEAD_ID")" || exit $?

SCHEMA="$(metadata_value "$SHOW_JSON" "gc.build.artifact_schema")"
PATH_KEYS="$(metadata_value "$SHOW_JSON" "gc.build.artifact_path_keys")"
[ -n "$SCHEMA" ] || fail "step metadata gc.build.artifact_schema is missing on $BEAD_ID"
[ -n "$PATH_KEYS" ] || fail "step metadata gc.build.artifact_path_keys is missing on $BEAD_ID"

ROOT_ID="$(metadata_value "$SHOW_JSON" "gc.root_bead_id")"
ROOT_JSON="$SHOW_JSON"
if [ -n "$ROOT_ID" ] && [ "$ROOT_ID" != "$BEAD_ID" ]; then
  ROOT_JSON="$(bd_show "$ROOT_ID")" || exit $?
fi

ARTIFACT_PATH=""
RESOLVED_KEY=""
IFS=',' read -r -a KEYS <<<"$PATH_KEYS"
for key in "${KEYS[@]}"; do
  key="$(printf '%s' "$key" | tr -d '[:space:]')"
  [ -n "$key" ] || continue
  value="$(metadata_value "$ROOT_JSON" "$key")"
  if [ -n "$value" ]; then
    ARTIFACT_PATH="$value"
    RESOLVED_KEY="$key"
    break
  fi
done
[ -n "$ARTIFACT_PATH" ] || fail "no artifact path recorded on workflow root ${ROOT_ID:-$BEAD_ID}; tried metadata keys: $PATH_KEYS. The producing stage must record the resolved artifact path before closing."

rig_root() {
  # Formula artifact paths are rig-relative. A producer runs in a disposable
  # per-bead worktree, so GC_WORK_DIR points at the wrong place whenever the
  # runtime provides the durable rig root. Controller checks use
  # GC_BEADS_SCOPE_ROOT on some runtimes, while agent sessions use
  # GC_RIG_ROOT. A legacy hand-copied check under <rig>/.gc/scripts/checks
  # derives the root from its own location. Do not use that fallback for a
  # source-tree or pack-cache script. The ralph controller runs the formula's
  # layer-resolved ../assets/scripts/checks/<name>.sh straight from the pinned
  # pack (gc-yrouy) and exports the owning store root (rig or city) as
  # GC_STORE_PATH, which is the durable root the artifact paths are relative to.
  local root="${GC_RIG_ROOT:-${GC_BEADS_SCOPE_ROOT:-${GC_DIR:-}}}"
  if [ -z "$root" ]; then
    local installed
    installed="$(cd "$SCRIPT_DIR/../../.." && pwd)"
    if [ -d "$installed/.gc" ]; then
      root="$installed"
    fi
  fi
  if [ -z "$root" ]; then
    root="${GC_STORE_PATH:-}"
  fi
  printf '%s' "$root"
}

case "$ARTIFACT_PATH" in
  /*) ;;
  *)
    ARTIFACT_ROOT="$(rig_root)"
    if [ -n "$ARTIFACT_ROOT" ]; then
      ARTIFACT_PATH="$ARTIFACT_ROOT/$ARTIFACT_PATH"
    else
      [ -n "${GC_WORK_DIR:-}" ] || fail "artifact path $ARTIFACT_PATH from $RESOLVED_KEY is relative and no rig-root environment is set"
      ARTIFACT_PATH="$GC_WORK_DIR/$ARTIFACT_PATH"
    fi
    ;;
esac
[ -f "$ARTIFACT_PATH" ] || fail "artifact $ARTIFACT_PATH from $RESOLVED_KEY does not exist"

VALIDATOR=""
for candidate in \
  ${GC_WORK_DIR:+"$GC_WORK_DIR/gascity/assets/scripts/validate_build_artifact.py"} \
  "$SCRIPT_DIR/../validate_build_artifact.py"; do
  if [ -n "$candidate" ] && [ -f "$candidate" ]; then
    VALIDATOR="$candidate"
    break
  fi
done
[ -n "$VALIDATOR" ] || fail "validate_build_artifact.py not found beside $SCRIPT_DIR or under GC_WORK_DIR"

VALIDATOR_ARGS=(--schema "$SCHEMA" --path "$ARTIFACT_PATH")
PERMIT_NOTE=""

if [ "$(metadata_value "$SHOW_JSON" "gc.build.coverage_permits")" = "required" ]; then
  VALIDATOR_ARGS+=(--require-coverage-permits)
  REQUIREMENTS_PATH=""
  # Walk from this step's workflow root up through the workflows that launched
  # it. An implementation item root is created by a drain control bead of the
  # parent workflow, and names it in gc.drain_control_id.
  WALK_JSON="$ROOT_JSON"
  WALK_ID="${ROOT_ID:-$BEAD_ID}"
  WALKED="$WALK_ID"
  for _hop in 1 2 3 4; do
    for key in gc.build.requirements_path gc.var.requirements_path; do
      value="$(metadata_value "$WALK_JSON" "$key")"
      [ -n "$value" ] || continue
      case "$value" in
        /*) ;;
        *)
          REQUIREMENTS_ROOT="$(rig_root)"
          [ -n "$REQUIREMENTS_ROOT" ] || REQUIREMENTS_ROOT="${GC_WORK_DIR:-}"
          [ -z "$REQUIREMENTS_ROOT" ] || value="$REQUIREMENTS_ROOT/$value"
          ;;
      esac
      [ -f "$value" ] || fail "requirements artifact $value recorded at $key on workflow root $WALK_ID does not exist; coverage permits cannot be checked"
      REQUIREMENTS_PATH="$value"
      break
    done
    [ -z "$REQUIREMENTS_PATH" ] || break
    CONTROL_ID="$(metadata_value "$WALK_JSON" "gc.drain_control_id")"
    [ -n "$CONTROL_ID" ] || break
    rc=0
    CONTROL_JSON="$(bd_show "$CONTROL_ID")" || rc=$?
    [ "$rc" -ne "$EXIT_NO_VERDICT" ] || exit "$rc"
    [ "$rc" -eq 0 ] || break
    PARENT_ID="$(metadata_value "$CONTROL_JSON" "gc.root_bead_id")"
    [ -n "$PARENT_ID" ] && [ "$PARENT_ID" != "$WALK_ID" ] || break
    rc=0
    WALK_JSON="$(bd_show "$PARENT_ID")" || rc=$?
    [ "$rc" -ne "$EXIT_NO_VERDICT" ] || exit "$rc"
    [ "$rc" -eq 0 ] || break
    WALK_ID="$PARENT_ID"
    WALKED="$WALKED, $WALK_ID"
  done
  if [ -n "$REQUIREMENTS_PATH" ]; then
    VALIDATOR_ARGS+=(--requirements "$REQUIREMENTS_PATH")
    PERMIT_NOTE=" requirements=$REQUIREMENTS_PATH"
  else
    # No root records a requirements artifact. An artifact with nothing left
    # open needs none; one that needs a permit is told what to record. The
    # quote is never checked against the work item or any other text.
    VALIDATOR_ARGS+=(--requirements-hint "no workflow root ($WALKED) records gc.build.requirements_path or gc.var.requirements_path. Record the requirements this build works from on the workflow root, then close the step again: gc bd update $WALK_ID --set-metadata gc.build.requirements_path=<absolute path to the requirements file>. If there is no requirements file, nothing can permit leaving a requirement open: do the work, or set the artifact status to blocked")
    PERMIT_NOTE=" requirements=unresolved"
  fi
fi

if OUTPUT="$(python3 "$VALIDATOR" "${VALIDATOR_ARGS[@]}" 2>&1)"; then
  echo "build artifact valid: schema=$SCHEMA path=$ARTIFACT_PATH$PERMIT_NOTE"
  exit 0
fi

echo "build-artifact-check: schema=$SCHEMA path=$ARTIFACT_PATH failed validation" >&2
printf '%s\n' "$OUTPUT" >&2
exit 1
