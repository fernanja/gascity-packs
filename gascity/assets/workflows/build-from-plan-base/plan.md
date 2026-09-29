This is the `build-from-plan-base` plan stage.

Produce or reuse the implementation plan using approved requirements from
`{{requirements_path}}`. Write the plan to `{{plan_path}}` when provided;
otherwise write the default implementation-plan artifact under
`{{artifact_root}}`.

Trace consumer call paths. When the requirements or design source name a
component or API that a page or other consumer will use (especially one an
earlier or sibling part delivered), record the exact call path (design
reference -> page/consumer -> wrapper -> primitive) and plan at least one test
at the consumer-facing layer, not only on the primitive. When this build is one
part of a multi-part effort, list each dependency on an earlier part's
deliverable with its concrete call path.

Name how each acceptance criterion is observed: the test, command, or
measurement that proves it, and confirm the test environment can actually
observe it (e.g. headless Chromium never draws classic scrollbars, so a
scrollbar-gutter width can only be modelled there). If it cannot, say so and
name the real verification instead (another environment or tool, a headed
run, or a documented manual check with evidence). Never fall back silently to
modelling, and never skip verification.

Stale bundled items are blocking. Re-check each bundled bead, bug report, or
cited `file:line`: `gc bd show <id>`, and the cited lines on the current base
branch. One closed with a fixing commit, or whose cited code changed since the
failure was recorded, is a blocking finding, never a non-blocking note: drop
it citing that evidence, or set `status: blocked` and stop for a decision.
Never relabel an earlier fix "partial" without evidence that it failed.

The plan must preserve requirement traceability, upstream hashes, assumptions,
risks, out-of-scope work, and verification strategy. Before closing this step,
resolve the workflow root bead id from `gc.root_bead_id` on this step bead, then
update THAT root bead's metadata (not this step bead's own metadata) with:
- `gc.build.plan_path=<the resolved plan artifact path>`
- `gc.var.plan_path=<the same resolved plan artifact path>`
- `gc.build.plan_sha256=<sha256 content hash of the artifact>`

The inherited `build-from-decompose-base` suffix reads `plan_path` as a required
var from workflow root metadata, and the artifact-validation gate below also
resolves the path from workflow root metadata — recording these values on this
step bead instead of the root satisfies neither and fails the run.

Artifact validation: this stage is gated by `.gc/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded on the WORKFLOW ROOT at `gc.build.plan_path` (fallback `gc.var.plan_path`) against schema `gc.build.plan.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact.
