Use the built-in Gas City `design-review` flow.

Run a plan review against the implementation plan. Treat required changes as blockers for decomposition; update the plan or capture the unresolved findings before closing this step.

Include a lightweight implementation readiness pass before decomposition:

- requirements traceability: every major plan task maps to acceptance criteria
- task boundaries: each task can become a clear implementation bead
- test commands: the plan names the focused proof commands or test strategy
- risk: risky files, migrations, public interfaces, and rollback concerns are
  explicit enough for an implementer

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
