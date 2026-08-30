This is the `build-from-plan-base` plan-review stage.

Review the implementation plan before decomposition. The verdict must map to
approved, questions, changes_required, or blocked, and it must honor
interaction_mode {{interaction_mode}}.

Write the plan-review artifact to `{{plan_review_path}}` when supplied;
otherwise write it under `{{artifact_root}}`. Before closing this step, resolve
the workflow root bead id from `gc.root_bead_id` on this step bead, then update
THAT root bead's metadata (not this step bead's own metadata) with
`gc.var.plan_review_path=<the resolved plan-review artifact path>`. The
inherited `build-from-decompose-base` suffix reads `plan_review_path` as a
required var from workflow root metadata — recording it only on this step bead
leaves that var empty and fails the handoff. Close only after an approved or
equivalent pass verdict is recorded, or after a blocked/changes-required verdict
is recorded with a concrete reason.
