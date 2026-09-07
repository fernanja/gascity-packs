This is the `build-from-decompose-base` decomposition stage.

Read the approved requirements from `{{requirements_path}}`, the approved implementation plan from `{{plan_path}}`, and the plan-review verdict from `{{plan_review_path}}`. Use the selected decomposition methodology `{{decomposition_formula}}` when translating the plan into durable work items.

Create or adopt an implementation convoy for the work units, explicitly scoped to
this workflow root's own store — read `gc.root_store_ref` on the workflow root
(e.g. `rig:ascent`) and pass the name after the colon as `--rig <name>` to `gc
convoy create` (omit `--rig` when the value is `city:...`, since that is the
default city scope). Do not rely on this step's own working directory to
resolve the correct store — this step runs in a `gc.task-decomposer` session,
which may not be scoped the same way as the workflow root. A convoy minted in
the wrong store silently strands workflow-finalize's later convoy-close step
(gc-wrg04h): it records a convoy ID whose prefix belongs to a different rig,
and that ID can never resolve when workflow-finalize runs against the root's
own store. The convoy must contain only runnable implementation beads for this
continuation; do not reuse any original request, planning, or workflow-control
convoy.

Write the decomposition artifact to `{{decomposition_path}}` when supplied; otherwise write it under `{{artifact_root}}` as the default decomposition artifact. The decomposition must include work item IDs, requirement and plan traceability, expected files or formula assets, verification expectations, dependencies, skipped work, and blocked work with rationale.

When a task's description references code or output produced by another numbered task in the same plan (e.g. task 4.2 needs the scaffold task 1.2 builds, or the API task 2.3 exposes), a real `bd dep <prerequisite-bead> --blocks <dependent-bead>` dependency must gate the dependent task's dispatch — do not rely on sequential or thematic numbering in the artifact's prose to imply that ordering. Sibling worktrees do not share code until merge, so a downstream task whose only ordering signal is prose numbering can be scheduled and dispatched in parallel with, or ahead of, work it structurally cannot proceed without, burning implementation attempts against a prerequisite that has not landed on the target branch yet (gc-6ccy3q). Wire these `bd dep` calls when creating the work-item beads below, using the traceability already captured in the decomposition artifact to identify which pairs need one.

Record the implementation convoy ID on the workflow root bead as both:

- `gc.input_convoy_id=<implementation-convoy-id>` for the drain contract.
- `gc.build.implementation_convoy_id=<implementation-convoy-id>` for continuation reporting.

Close this step only after the decomposition artifact is recorded and both convoy metadata fields are set. Verify the recorded implementation convoy is not the original launch or workflow-control convoy.

Artifact validation: this stage is gated by `.gc/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.decomposition_path` (fallback `gc.var.decomposition_path`) against schema `gc.build.decomposition.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact.
