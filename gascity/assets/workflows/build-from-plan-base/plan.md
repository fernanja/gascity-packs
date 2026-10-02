This is the `build-from-plan-base` plan stage.

Produce or reuse the implementation plan using approved requirements from
`{{requirements_path}}`. Write the plan to `{{plan_path}}` when provided;
otherwise write the default implementation-plan artifact under
`{{artifact_root}}`.

Trace consumer call paths. When the requirements or design source name a
component or API that a page or other consumer will use (especially one an
earlier or sibling part delivered), record the exact call path (design
reference -> page/consumer -> wrapper -> primitive) and plan at least one test
at the consumer-facing layer, not only on the primitive. When this build is one
part of a multi-part effort, list each dependency on an earlier part's
deliverable with its concrete call path.

Name how each acceptance criterion is observed: the test, command, or
measurement that proves it, and confirm the test environment can actually
observe it (e.g. headless Chromium never draws classic scrollbars, so a
scrollbar-gutter width can only be modelled there). If it cannot, say so and
name the real verification instead (another environment or tool, a headed
run, or a documented manual check with evidence). Never fall back silently to
modelling, and never skip verification.

Account for every requirement. List each requirement and acceptance-criterion
id of the requirements artifact under that artifact's `trace.upstream[].ids`,
and give each a `trace.coverage` entry. The gate reads the ids from
`{{requirements_path}}` itself: labels such as `AC-1`, `SCOPE-2`, `REQ-3` that
lead a list item, a heading or a paragraph, except under a heading that says
out of scope, non-goals, open questions, background or verified. An `AC-`,
`SCOPE-` or `REQ-` id left out of both lists is rejected, not overlooked;
other labels (`OQ-`, `OOS-`, `CON-`) need an entry only if you list them.
`covered` means this plan
delivers it and names how it is observed. Any other status (`deferred`,
`blocked`, `out_of_scope`, `not_applicable`, `superseded`) needs, beside its
`rationale`, a `permit`: the sentence in the requirements artifact that hands
the requirement off, quoted word for word, at least 20 characters:

```yaml
- id: AC-3
  status: out_of_scope
  rationale: The alerts close only after merge; the plan lists them for the mayor.
  permit: "The mayor checks this post-merge; it is not a worker AC."
```

The gate checks each quote against `{{requirements_path}}` (ignoring line
wrapping, letter case and Markdown marks) and rejects the plan when a permit is
missing, its text is not there, or the quoted text hands nothing off (gc-gdyaz:
a plan postponed a required check with a footnote, was approved, and the gap
cost a three-hour fix loop). The quote must itself say the requirement is for
later, for someone else, or not for this work: "post-merge", "after merge",
"next round", "follow-up bead", "out of scope", "not in this work", "do not
touch", "mayor-owned", "the mayor checks", "Jon's decision", "deferred",
"blocked on". A requirement's own statement is in the requirements too, and it
is not a permit.

- A criterion the requirements give to someone else or to a later moment (a
  post-merge check the mayor owns, a step the publish stage performs) is
  permitted by the sentence that says so. Quote it.
- An exclusion the requirements state is `out_of_scope`, with the exclusion
  sentence as its permit.
- A requirement already satisfied on the base branch, or one that asks for no
  change, is `covered`, with the commit or the check as its evidence. It is
  not a deferral.
- A conditional requirement ("If only the test is wrong: ...") whose condition
  does not hold is `not_applicable`. Its `permit` quotes the requirement's own
  conditional clause (the "if", "when" or "unless" part) and its `rationale`
  says why the condition is false. This is the one case where a requirement's
  own words are its permit.
- Your own notes, discoveries and non-goals are not requirements. Keep them
  out of `ids` and coverage; they belong under `## Non-Goals` and the risks.
- If the requirements do not allow leaving something out and the plan cannot
  deliver it, do not approve the plan around it. Set `status: blocked` (or
  `questions`), state the decision needed, and stop. A plan that is not
  approved needs no permits.

Stale bundled items are blocking. Re-check each bundled bead, bug report, or
cited `file:line`: `gc bd show <id>`, and the cited lines on the current base
branch. One closed with a fixing commit, or whose cited code changed since the
failure was recorded, is a blocking finding, never a non-blocking note: drop
it citing that evidence, or set `status: blocked` and stop for a decision.
Never relabel an earlier fix "partial" without evidence that it failed.

The plan must preserve requirement traceability, upstream hashes, assumptions,
risks, out-of-scope work, and verification strategy. Before closing this step,
resolve the workflow root bead id from `gc.root_bead_id` on this step bead, then
update THAT root bead's metadata (not this step bead's own metadata) with:
- `gc.build.plan_path=<the resolved plan artifact path>`
- `gc.var.plan_path=<the same resolved plan artifact path>`
- `gc.build.plan_sha256=<sha256 content hash of the artifact>`

The inherited `build-from-decompose-base` suffix reads `plan_path` as a required
var from workflow root metadata, and the artifact-validation gate below also
resolves the path from workflow root metadata — recording these values on this
step bead instead of the root satisfies neither and fails the run.

Artifact validation: this stage is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded on the WORKFLOW ROOT at `gc.build.plan_path` (fallback `gc.var.plan_path`) against schema `gc.build.plan.v1` and checks every coverage permit against the requirements artifact the workflow root records (`gc.build.requirements_path`, fallback `gc.var.requirements_path`). On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact.
