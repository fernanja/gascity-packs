This is the `build-from-plan-base` plan-review stage.

Review the implementation plan before decomposition. The verdict must map to
approved, questions, changes_required, or blocked, and it must honor
interaction_mode {{interaction_mode}}.

Treat a deferred requirement as a blocking finding. Go through every
requirement and acceptance criterion in the requirements artifact and find
where the plan delivers it. If the plan marks one as deferred, out of scope,
report-only, "later", or to be done by another stage, the verdict is
`changes_required` unless the requirements artifact itself permits that
deferral in so many words. Name the criterion and quote the plan sentence. A
criterion the plan never mentions is the same finding (gc-gdyaz: a plan
postponed a required check, the plan review approved it, and the code review
found the gap three hours later).

Check scope reachability: confirm every acceptance criterion can be met along
the plan's traced call path without breaking the requirements' scope
constraints (e.g. "no edits under X"). If a scope ban makes a criterion
unreachable, record a blocking finding naming the criterion, the ban, and the
call-path hop that needs the edit. Never approve around the conflict.

Treat an acceptance criterion with no observation method, or one the test
environment cannot observe with no stated alternative verification, as a
blocking finding.

Mark every specific, actionable note (e.g. "measure first", "X is the page's
responsibility") as `advisory` or `must-address`, and list the must-address
notes under `## Must-Address Notes` in the plan-review artifact.
Decomposition copies each one into its owning work item as a checklist item.

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

## Closing this step

Always set `gc.outcome` on this step's own claimed bead before closing it.
This step is a member of the build's planning scope: closing it with
`gc.outcome=fail` stops the build.

A review that reached a verdict closes with `gc.outcome=pass`, whatever the
verdict: `gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`,
then `gc bd close "<claimed-step-id>" --reason "<verdict and one-line reason>"`.
The verdict lives in the plan-review artifact. The next step,
`prepare-decompose`, reads it and stops the build when it is not approved.

Close with `gc.outcome=fail` only when no verdict could be produced, for
example because the plan artifact is missing or unreadable. Record the stop on
the workflow root first: `gc.build.status=blocked`,
`gc.build.blocked_step=plan-review`, `gc.failure_class=plan_review_failed`,
`gc.restart.entrypoint=build-from-plan`, and `gc.restart.reason` with one
machine-readable clause.
