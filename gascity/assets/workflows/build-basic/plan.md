Use the built-in Gas City design authoring flow.

Create the implementation plan at the path recorded on the workflow root bead
as `gc.build.plan_path` (fallback `gc.var.plan_path`). The artifact must be
Markdown with YAML front matter, not JSON. Its front matter must declare
`schema: gc.build.plan.v1`, the workflow id/formula, the methodology pack/name,
the producer formula/stage/attempt, `status`, and `trace` with upstream and
coverage entries. Include a Markdown coverage table whose ID/status pairs
exactly match `trace.coverage`.
The validator only recognizes a Markdown table with an `ID` column and a
`Status` column. Use this shape:

| ID | Status |
| --- | --- |
| REQ-001 | covered |

Use mapping objects for front matter; do not use scalar shortcuts such as
`workflow: build-basic`. The top-level YAML shape must be:

- `schema: gc.build.plan.v1`
- `workflow: {id: <workflow-root-id>, formula: build-basic}`
- `methodology: {pack: gascity, name: build-basic}`
- `producer: {formula: build-basic, stage: plan, attempt: <positive integer>}`
- `status: approved` or another schema-allowed status
- `trace: {upstream: [...], coverage: [...]}`

Trace front matter must use the validator shape exactly:

- `trace.upstream[]` entries must include `path` and `hash`; do not use
  `id`/`title`/`type` entries as the upstream shape.
- For the requirements artifact, use its recorded path and a scheme-qualified
  hash such as `sha256:<digest>` or `git:<revision>`.
- If an upstream entry lists `ids`, every listed id must appear exactly once in
  `trace.coverage` and in the Markdown coverage table with the same status.
- Coverage statuses are not artifact statuses. Use `covered` for satisfied
  requirements; do not use `approved` in `trace.coverage[].status` or the
  Markdown coverage table.

Ground the plan in the generated requirements and repository conventions.
Include the required schema sections:

- Summary
- Current System
- Proposed Implementation
- Non-Goals
- Verification

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

Stale bundled items are blocking. Re-check each bundled bead, bug report, or
cited `file:line`: `gc bd show <id>`, and the cited lines on the current base
branch. One closed with a fixing commit, or whose cited code changed since the
failure was recorded, is a blocking finding, never a non-blocking note: drop
it citing that evidence, or set `status: blocked` and stop for a decision.
Never relabel an earlier fix "partial" without evidence that it failed.

Record the implementation plan path on the workflow root bead before closing.
Use `gc bd update "<workflow-root-id>" --set-metadata "gc.build.plan_path=<absolute path>"`.
Do not use `gc bd update --metadata 'key=value'`; `--metadata` only accepts a JSON
object.
Before closing this step, set the claimed step outcome with
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then close
with `gc bd close "<claimed-step-id>" --reason "<concise reason>"`. Do not pass
`--metadata` or `--set-metadata` to `gc bd close`.

Artifact validation: this stage is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.plan_path` (fallback `gc.var.plan_path`) against schema `gc.build.plan.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact.
