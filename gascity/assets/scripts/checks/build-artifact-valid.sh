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
#   gc.build.coverage_permits=required - the artifact may leave a requirement at
#     a status other than "covered" only with a `permit` quoting the
#     requirements artifact (gc-gdyaz). The requirements artifact is the one the
#     workflow root records (gc.build.requirements_path, then
#     gc.var.requirements_path), never one the artifact names for itself. A
#     workflow with no requirements artifact (a bare implementation convoy)
#     falls back to the text of its source work item.

fail() {
  echo "build-artifact-check: $*" >&2
  exit 1
}

BEAD_ID="${GC_BEAD_ID:-}"
[ -n "$BEAD_ID" ] || fail "GC_BEAD_ID is required"
command -v gc >/dev/null 2>&1 || fail "gc is required on PATH"
command -v python3 >/dev/null 2>&1 || fail "python3 is required on PATH"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

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

SHOW_JSON="$(gc bd show "$BEAD_ID" --json 2>/dev/null)" || fail "gc bd show $BEAD_ID failed"

SCHEMA="$(metadata_value "$SHOW_JSON" "gc.build.artifact_schema")"
PATH_KEYS="$(metadata_value "$SHOW_JSON" "gc.build.artifact_path_keys")"
[ -n "$SCHEMA" ] || fail "step metadata gc.build.artifact_schema is missing on $BEAD_ID"
[ -n "$PATH_KEYS" ] || fail "step metadata gc.build.artifact_path_keys is missing on $BEAD_ID"

ROOT_ID="$(metadata_value "$SHOW_JSON" "gc.root_bead_id")"
ROOT_JSON="$SHOW_JSON"
if [ -n "$ROOT_ID" ] && [ "$ROOT_ID" != "$BEAD_ID" ]; then
  ROOT_JSON="$(gc bd show "$ROOT_ID" --json 2>/dev/null)" || fail "gc bd show $ROOT_ID failed"
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
PERMIT_TMP=""
cleanup() {
  [ -z "$PERMIT_TMP" ] || rm -rf "$PERMIT_TMP"
}
trap cleanup EXIT

if [ "$(metadata_value "$SHOW_JSON" "gc.build.coverage_permits")" = "required" ]; then
  VALIDATOR_ARGS+=(--require-coverage-permits)
  REQUIREMENTS_PATH=""
  for key in gc.build.requirements_path gc.var.requirements_path; do
    value="$(metadata_value "$ROOT_JSON" "$key")"
    [ -n "$value" ] || continue
    case "$value" in
      /*) ;;
      *)
        REQUIREMENTS_ROOT="$(rig_root)"
        [ -n "$REQUIREMENTS_ROOT" ] || REQUIREMENTS_ROOT="${GC_WORK_DIR:-}"
        [ -z "$REQUIREMENTS_ROOT" ] || value="$REQUIREMENTS_ROOT/$value"
        ;;
    esac
    [ -f "$value" ] || fail "requirements artifact $value recorded at $key on workflow root ${ROOT_ID:-$BEAD_ID} does not exist; coverage permits cannot be checked"
    REQUIREMENTS_PATH="$value"
    break
  done
  if [ -n "$REQUIREMENTS_PATH" ]; then
    VALIDATOR_ARGS+=(--requirements "$REQUIREMENTS_PATH")
    PERMIT_NOTE=" permits=$REQUIREMENTS_PATH"
  else
    # No requirements artifact on this workflow: the source work item is the
    # only statement of what was asked, so permits quote it.
    SOURCE_ID="$(metadata_value "$ROOT_JSON" "gc.drain_member_id")"
    [ -n "$SOURCE_ID" ] || SOURCE_ID="$(metadata_value "$ROOT_JSON" "gc.input_convoy_id")"
    if [ -n "$SOURCE_ID" ] && SOURCE_JSON="$(gc bd show "$SOURCE_ID" --json 2>/dev/null)"; then
      PERMIT_TMP="$(mktemp -d)"
      printf '%s' "$SOURCE_JSON" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
if isinstance(data, list):
    data = data[0] if data else {}
if not isinstance(data, dict):
    raise SystemExit(0)
for field in ("title", "description", "acceptance_criteria", "design", "notes"):
    value = data.get(field)
    if isinstance(value, str) and value.strip():
        print(value)
        print()
' >"$PERMIT_TMP/work-item-$SOURCE_ID.md"
      VALIDATOR_ARGS+=(--requirements "$PERMIT_TMP/work-item-$SOURCE_ID.md")
      PERMIT_NOTE=" permits=work-item:$SOURCE_ID"
    fi
  fi
fi

if OUTPUT="$(python3 "$VALIDATOR" "${VALIDATOR_ARGS[@]}" 2>&1)"; then
  echo "build artifact valid: schema=$SCHEMA path=$ARTIFACT_PATH$PERMIT_NOTE"
  exit 0
fi

echo "build-artifact-check: schema=$SCHEMA path=$ARTIFACT_PATH failed validation" >&2
printf '%s\n' "$OUTPUT" >&2
exit 1
