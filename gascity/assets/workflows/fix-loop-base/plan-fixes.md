This is the `fix-loop-base` methodology contract fix-planning step.

Concrete methodology packs override this step to convert `{{findings_path}}`
into their native fix plan with optional context from `{{context_path}}`. The
adapter remains responsible for external publication and lifecycle state.

Plan each fix at the level of the mechanism, not the instance the review named.

- For each finding, state the class of defect it belongs to (for example "a
  wait that the previous state can already satisfy", or "cleanup that assumes
  the request has finished") and name every other place in the files this
  change touches where the same class occurs. The plan fixes all of them in
  this pass.
- Prefer a fix that makes the defect impossible by construction over one that
  moves a threshold. A longer timeout, a wider window, one more retry or one
  more special case patches the named instance; the next review finds the
  neighbouring instance and the loop runs again. If a threshold change really
  is the right fix, say why no structural fix exists.
- Read the earlier fix plans and review reports in the artifact root. If this
  finding is a narrower version of an earlier one, the earlier approach has
  failed: do not refine it again. Change the approach, and say in the plan what
  was wrong with the previous one.
- List what the fix deliberately does not cover, so the re-review can judge
  that boundary instead of discovering it.

Why: one round of this loop costs most of an hour even for a one-line change.
One test went through five rounds because each plan closed exactly the hole
named and nothing beside it (gc-f6est).
