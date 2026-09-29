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

Do not close the workflow root with `gc.outcome=pass` when the review verdict
is `blocked` or `changes_required`, any implementation drain failed, required
implementation evidence is missing, or `gc.build.repair_status` is anything
other than `not_needed` or `approved`. In those cases, write a final report with
`status: blocked`, record `gc.outcome=fail`, `gc.build.status=blocked`,
`gc.failure_class` with the machine-readable reason, and preserve restart
metadata such as `gc.restart.entrypoint`, `gc.restart.reason`, and the relevant
artifact paths on the workflow root — then also close THIS step's own claimed
bead with the same outcome: `gc bd update "<claimed-step-id>" --set-metadata
"gc.outcome=fail"`, then `gc bd close "<claimed-step-id>" --reason "<concise
reason>"`. Synthesizing a correct blocked report is not the same as the
underlying work being approved — do not leave this step's own claimed bead at
a default/pass outcome while reporting blocked on the root.

Only record a passing terminal outcome (on both the workflow root and this
step's own claimed bead) when all prerequisite artifacts exist, implementation
evidence is present, review is approved, and repair status is `not_needed` or
`approved`.

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
