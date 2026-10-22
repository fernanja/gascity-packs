This is the `build-base` plan-review stage. Treat it as a virtual contract that concrete formulas may override.

Review the plan for traceability to requirements, feasibility, missing edge cases, and implementation readiness. If changes are required, update or route the plan before closing this stage.

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

Close this step only when the plan is approved or the blocking issues are recorded in the step summary.

Before closing this step, set the claimed step outcome with
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then close
with `gc bd close "<claimed-step-id>" --reason "<concise reason>"`. Do not pass
`--metadata` or `--set-metadata` to `gc bd close`.
