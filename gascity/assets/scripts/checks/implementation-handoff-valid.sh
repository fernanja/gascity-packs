#!/usr/bin/env bash
set -euo pipefail

# Implementation handoff gate: the step that ends implementation closes only
# when (1) its summary artifact is valid and (2) the commit it hands to review
# is the head of an open pull request with complete, green checks.
#
# A step carries one [steps.check] script, so this chains the two gates in
# order, as preflight-evidence-valid.sh chains the artifact gate. Both read
# the same $GC_BEAD_ID. Each prints its own machine-readable failure lines on
# stderr for gc.attempt_log; the first failure stops the chain.
#
#   build-artifact-valid.sh  summary schema, coverage and coverage permits
#   pr-ci-green.sh           CI-green handoff (gc-68exu); passes with an explicit
#                            "skipped: <reason>" when the workflow does not
#                            publish or the remote is not GitHub

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
for gate in build-artifact-valid.sh pr-ci-green.sh; do
  [ -x "$SCRIPT_DIR/$gate" ] || {
    echo "implementation-handoff-check: $gate is missing or not executable beside $SCRIPT_DIR" >&2
    exit 1
  }
done

"$SCRIPT_DIR/build-artifact-valid.sh" || exit 1
"$SCRIPT_DIR/pr-ci-green.sh" || exit 1
