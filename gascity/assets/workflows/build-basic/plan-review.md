Use the built-in Gas City `design-review` flow.

Run a plan review against the implementation plan. Treat required changes as blockers for decomposition; update the plan or capture the unresolved findings before closing this step.

Include a lightweight implementation readiness pass before decomposition:

- requirements traceability: every major plan task maps to acceptance criteria
- task boundaries: each task can become a clear implementation bead
- test commands: the plan names the focused proof commands or test strategy
- risk: risky files, migrations, public interfaces, and rollback concerns are
  explicit enough for an implementer

Check scope reachability: confirm every acceptance criterion can be met along
the plan's traced call path without breaking the requirements' scope
constraints (e.g. "no edits under X"). If a scope ban makes a criterion
unreachable, record a blocking finding naming the criterion, the ban, and the
call-path hop that needs the edit. Never approve around the conflict.

Mark every specific, actionable note (e.g. "measure first", "X is the page's
responsibility") as `advisory` or `must-address`, and list the must-address
notes under `## Must-Address Notes` in the plan-review artifact
(the plan-readiness note below; write it whenever such notes exist).
Decomposition copies each one into its owning work item as a checklist item.

If you write a plan-readiness note, record it on the workflow root as
`gc.build.plan_review_report_path=<path>`. Do not write or overwrite
`gc.build.review_report_path`; that key is reserved for the later
build-basic implementation review artifact.

Before closing this step, set the claimed step outcome with
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then close
with `gc bd close "<claimed-step-id>" --reason "<concise reason>"`. Do not pass
`--metadata` or `--set-metadata` to `gc bd close`.
