#!/usr/bin/env bash
set -euo pipefail

# Implementation handoff gate: the step that ends implementation closes only
# when (1) its summary artifact is valid and (2) the commit it hands to review
# is recorded (reviewer-first, gc-gef2p8: CI is deferred to the publish step
# while no pull request exists; a pull request that does exist must have
# complete, green checks).
#
# A step carries one [steps.check] script, so this chains the three gates in
# order, as preflight-evidence-valid.sh chains the artifact gate. All read
# the same $GC_BEAD_ID. Each prints its own machine-readable failure lines on
# stderr for gc.attempt_log; the first failure stops the chain.
#
#   build-artifact-valid.sh  summary schema, coverage and coverage permits
#   handoff-commit-ancestor.sh
#                            the hand-off keys (gc-jhuzd0): when the workflow
#                            publishes, gc.build.handoff_commit and
#                            gc.build.handoff_branch must be recorded on the item
#                            root, the commit on the branch, tagged [skip ci] and
#                            on origin. A missing key fails the close and the
#                            message names the two `gc bd update` calls. Skips
#                            when the workflow does not publish.
#   pr-ci-green.sh           hand-off gate (gc-68exu, gc-gef2p8); passes with
#                            "reviewer-first: CI deferred to publish" while no
#                            pull request exists, or an explicit
#                            "skipped: <reason>" when the workflow does not
#                            publish or the remote is not GitHub

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
for gate in build-artifact-valid.sh handoff-commit-ancestor.sh pr-ci-green.sh; do
  [ -x "$SCRIPT_DIR/$gate" ] || {
    echo "implementation-handoff-check: $gate is missing or not executable beside $SCRIPT_DIR" >&2
    exit 1
  }
done

# Exit codes pass through unchanged: 75 from either gate means "no verdict"
# (an infrastructure error), not a failed artifact or a red check. The last
# gate replaces this shell, so a check timeout ends the gate itself rather
# than leaving it running behind a dead wrapper.
"$SCRIPT_DIR/build-artifact-valid.sh" || exit $?
"$SCRIPT_DIR/handoff-commit-ancestor.sh" || exit $?
exec "$SCRIPT_DIR/pr-ci-green.sh"
