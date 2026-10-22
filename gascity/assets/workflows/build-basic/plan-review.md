Use the built-in Gas City `design-review` flow.

Run a plan review against the implementation plan. Treat required changes as blockers for decomposition; update the plan or capture the unresolved findings before closing this step.

Include a lightweight implementation readiness pass before decomposition:

- requirements traceability: every major plan task maps to acceptance criteria
- task boundaries: each task can become a clear implementation bead
- test commands: the plan names the focused proof commands or test strategy
- risk: risky files, migrations, public interfaces, and rollback concerns are
  explicit enough for an implementer

Check the requirements and the plan against the rig's product invariants
(gc-4asa1t; R19 shipped `isAccountType = row.level === 0` because a design
prompt said so and nothing checked it). Before the verdict, read the section
titled "Product Invariants" in the rig root `CLAUDE.md`. When the file or the
section is absent, say so in the review. Check both the requirements and the
plan against each invariant. A requirement or plan step that contradicts an
invariant is a `must-address` note naming the invariant and the conflicting
text, and the plan is corrected to follow the invariant: the invariant wins
over requirements text, design prompts and boards. When the conflict cannot be
resolved inside the plan (the requirements demand the violation), the verdict
is `changes_required` and you mail the mayor at once with both texts.

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

A check, test or probe in the plan that cannot fail when the mechanism it
verifies is wrong is `must-address`, never `advisory`. It cannot fail when it
exercises only the path the mechanism itself implements (a checksum loop that
prints ok for a swapped archive; a crash-sweep probe that only runs when no
crash happened). Name the counter-case to add in the note: the input or state
for which the mechanism is wrong and the check must fail. Plan review rated
these advisory twice and both reached review as defects (native-verification
checksum loop, gcas-8kiek63 implementation-plan.md:247-251; test-slot crash
sweep probe, gcas-fip716u :270-273 and :401-404).

Re-run the plan's consumer searches. The plan lists, for every changed
interface, test id, selector, helper, CLI flag, file path or config key, all of
its consumers with the exact search command and its hit count. Re-run at least
one of those searches yourself, on the current base branch, and compare the hit
count. A count that differs, or a changed item that has no listed search, is a
`must-address` note that names the search and both counts.

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
