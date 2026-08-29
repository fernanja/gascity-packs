This is the `build-from-review-base` publish stage.

Read push {{push}}, open_pr {{open_pr}}, and the finalized continuation outcome
from the workflow root metadata. If neither publishing action is explicitly
authorized, no-op and record `not_published`.

Publishing disabled or no-op status must never convert a blocked, failed, or
repairable finalization into a passing workflow outcome. Preserve
`gc.outcome=fail`, `gc.build.status=blocked`, `gc.failure_class`, and
`gc.restart.*` metadata when finalize recorded them.

This step is the workflow's terminal step for outcome purposes: whether the
whole workflow reads as pass or fail is derived from THIS step's own claimed
bead outcome, not just from the workflow root's metadata. Completing this
step without an internal error (a correct no-op when publishing is disabled,
or a correctly-skipped publish because finalize recorded a block) is not the
same thing as the reviewed work being approved -- do not close THIS step's
own claimed bead with `gc.outcome=pass` just because the step itself ran
cleanly. When finalize recorded a blocked, failed, or repairable state, close
THIS step's own claimed bead with the same outcome: `gc bd update
"<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then `gc bd close
"<claimed-step-id>" --reason "<concise reason, e.g. publish correctly
skipped -- workflow blocked upstream>"`. Only set `gc.outcome=pass` on this
step when finalize recorded an approved, passing continuation.

If publishing is authorized, publish only after the continuation finalized
successfully and the review stage approved or explicitly allowed publication.
Record push status, PR status, or a blocked publish reason on the workflow root
and publish step before closing.
