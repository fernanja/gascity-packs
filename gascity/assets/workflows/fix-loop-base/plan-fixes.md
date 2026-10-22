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
- Do not plan a fix for an item the review lists under `Permitted/environment`
  or `Pre-existing or flaky`: those are not findings (gc-aecuvz). Plan only the
  findings.
- A fix that fixes one instance of a demonstrated defect class lists every other
  instance of that class with the search that found them (the command and its
  hit count), and fixes them in the same pass or says why one is out of reach.
  A plan that fixes the named instance and lists no search is incomplete
  (product-fixes, bug-bash round 8: each round fixed the instance named and the
  next review found its neighbour).
- Any timeout, wall-clock bound, cap or wait the plan prescribes for a gate,
  suite or command must be at least that gate's own configured timeout, and the
  plan cites where that timeout is defined (file and line, or the config key).
  A bound shorter than the gate fails a correct run and earns another loop
  (three builds looped on a 1200, 1800 and 300 second cap below the gate:
  gcas-h90y68w, gcas-qe32i81 and gcas-3msxtwu, fix-loop deep dive 2026-10-22).
  When you cannot find the gate's timeout, do not invent a smaller one:
  prescribe no bound and say so.
- List what the fix deliberately does not cover, so the re-review can judge
  that boundary instead of discovering it.

Why: one round of this loop costs most of an hour even for a one-line change.
One test went through five rounds because each plan closed exactly the hole
named and nothing beside it (gc-f6est).
