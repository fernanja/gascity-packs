This is the `planning-base` methodology contract plan-review step.

Concrete methodology packs override this step to approve, revise, or reject the
plan artifact. Close pass only after the plan artifact is approved and ready for
the decomposition contract.

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

The requested review authority is `{{review_mode}}`: in `report` mode, record
findings and a verdict without mutating the plan; in `agent` mode, also produce
a structured fix handoff for the planning author to apply; in `interactive`
mode, safe plan fixes may be applied directly with every change and reason
recorded. The interaction posture is `{{interaction_mode}}`; in `headless`
mode, never ask questions and stop blocked with a machine-readable
`gc.blocked_reason` when approval input is missing.
