This is the `fix-loop-base` methodology contract re-review step.

Concrete methodology packs override this step to call `{{code_review_formula}}`
after fixes. Continue only while the iteration count is below
`{{max_iterations}}`. Record the follow-up review report path on workflow root
metadata as `gc.build.review_report_path` before closing.

Report the class of a defect, not only the instance. When you find one, look
for every other occurrence of the same kind in the diff and in the code it
touches, and list them all in the same finding (file and line for each), so
one fix pass can close the class. Say what would make the defect impossible,
not only what is wrong with this line. Naming one instance while its
neighbours are in view costs a full extra round for each of them (gc-f6est:
one test was sent back five times, one hole per review).

When a finding is a narrower version of one raised earlier in this artifact
root, say so, and state the acceptance test that settles the whole class: the
condition under which it will not be raised again.

Artifact validation: this step is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the report recorded at `gc.build.review_report_path` against schema `gc.build.review.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the report in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the report.
