This is the `build-from-review-base` finalize stage.

Synthesize the continuation result from the prerequisite artifacts,
implementation evidence, review reports, fix attempts, drift checks, and
publish intent.

The final report must state which continuation entrypoint started the run and
which upstream stages were skipped because their approved artifacts already
existed. Include the requirements path, plan path, plan-review path when
available, decomposition path, implementation convoy ID when available,
implementation evidence, review verdict, remaining risk, publish
authorization, and next action.

Do not close the workflow root with `gc.outcome=pass` when an earlier build
step stopped the build, the review verdict is `blocked` or `changes_required`,
any implementation drain failed, required implementation evidence is missing,
or `gc.build.repair_status` is anything other than `not_needed` or `approved`.
In those cases, write a final report with `status: blocked`, record
`gc.outcome=fail`, `gc.build.status=blocked`, `gc.failure_class` with the
machine-readable reason, and preserve restart metadata such as
`gc.restart.entrypoint`, `gc.restart.reason`, and the relevant artifact paths
on the workflow root.

Only record a passing terminal outcome on the workflow root when all
prerequisite artifacts exist, implementation evidence is present, review is
approved, and repair status is `not_needed` or `approved`.

**When an earlier step stopped the build.** The build steps before this one
sit in two scopes: planning (everything before the implementation drain) and
review (`prepare-review`, `review`, `repair-review`). When a planning step
closes with a failed outcome, the engine skips the rest of planning, the drain
closes without running, `prepare-review` ends the review scope, and this step
runs next. When `prepare-review` or `review` fails, the engine skips what is
left of the review scope. This step can therefore start with whole stages
never run: no approved plan review, no decomposition, no implementation, no
review. That is the expected shape of a stopped build. Do not run or repair
the skipped stages, and do not report a skipped stage's missing artifact as
the cause. In the final report, list under `trace.upstream` only the artifacts
that exist, and give each requirement the build did not deliver the coverage
status `blocked`.

Read the workflow root before writing anything. If it already carries a
`gc.failure_class` or a `gc.restart.entrypoint`, a step recorded the stop: a
`prepare-*` gate, `plan-review`, or `repair-review` (`gc.build.blocked_step`
names it). Keep the recorded `gc.failure_class`, `gc.restart.entrypoint`, and
`gc.restart.reason` exactly as recorded, and add `gc.build.status=blocked` when
it is missing. Never replace them with a later symptom such as missing
implementation evidence: the first stop is the cause, and its entrypoint is
where the build restarts (gc-2ua7i: a build stopped by a rejected plan ended
with `gc.restart.entrypoint=build-from-review`).

Only when the root carries neither value did no step record the stop: a
checked stage ran out of attempts, or a step closed failed without recording.
Record the stop here, from the first stage in build order that has no valid
result:

| First stage with no valid result | `gc.failure_class` | `gc.restart.entrypoint` |
| --- | --- | --- |
| requirements artifact | `requirements_artifact_invalid` | `build-from-requirements` |
| plan artifact | `plan_artifact_invalid` | `build-from-plan` |
| plan review (missing or not approved) | `plan_review_not_approved` | `build-from-plan` |
| decomposition artifact or implementation convoy | `decomposition_artifact_invalid` | `build-from-decompose` |
| implementation evidence | `implementation_evidence_missing` | `build-from-convoy` |
| review report | `review_artifact_invalid` | `build-from-review` |

**This step's own claimed bead.** The final report is this step's work
product. Once a final report that validates is written and the terminal
metadata is on the workflow root, close this bead with `gc.outcome=pass`:
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then
`gc bd close "<claimed-step-id>" --reason "<concise reason>"`. That holds for a
`status: blocked` report as well. Do not close this bead with `gc.outcome=fail`
because the build is blocked: the artifact check counts a failed bead as a
failed attempt without reading the report, and the engine dispatches this step
again, up to three times, to write the same report (gc-2ua7i). The build still
fails. The workflow root closes `fail` from the failed scope and from the
publish step, which reads `gc.build.status` on the root. Close this bead with
`gc.outcome=fail` only when you could not write a valid final report at all.

Before recording a *failing* terminal outcome because `gc.build.repair_status`
is anything other than `not_needed`/`approved`, independently re-open the
artifact at `gc.build.review_report_path` and read its own verdict/status
field — do not trust `gc.build.repair_status` or `gc.build.review_verdict`
metadata alone. If that artifact's own verdict is `approved` with zero
unresolved findings, the metadata is stale or wrong: do not close as
`gc.outcome=fail`. Record `gc.failure_class=repair_status_contradicts_review`
instead, leave the workflow root open with a report explaining the
contradiction and citing both the metadata values and the artifact path/
verdict you read, and stop for repair-review to reconcile rather than
finalizing on inconsistent inputs (this contradiction is exactly what shipped
as a false failure in gc-jxl5x: workflow root gcas-gigyfp).

Record terminal outcome metadata on the workflow root before closing so the
publish step can safely no-op, push, open a PR, or block with an explicit
reason without changing the workflow outcome. Do not blank, reset, or
otherwise touch `gc.build.review_subject_commit` -- the publish step trusts
it as the sole source of the approved commit (gc-ajt3i) and it must still
hold whatever value review/repair-review last recorded against the approving
verdict.

Artifact validation: this stage is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.final_report_path` against schema `gc.build.final-report.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact.
