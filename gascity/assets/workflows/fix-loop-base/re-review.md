This is the `fix-loop-base` methodology contract re-review step.

Concrete methodology packs override this step to call `{{code_review_formula}}`
after fixes. Continue only while the iteration count is below
`{{max_iterations}}`. Record the follow-up review report path on workflow root
metadata as `gc.build.review_report_path` before closing.

Start with the automated checks, before reading any code. Find the pull
request for the work branch (`gh pr list --head <branch>`) and read its checks
for the commit under review (`gh pr checks <number>`). If checks are still
running, wait for them (`gh pr checks <number> --watch`) rather than reviewing
a commit whose result is unknown. Every failed check on that commit is a
required finding: name the job, the failing step and the decisive log lines,
and return `changes_required` without spending the review on problems a
machine has already reported (gc-68exu). Call a failure unrelated to the diff
only with evidence: the same step failing on the default branch, or passing on
a rerun of the same commit. If no pull request or no check run exists for the
commit, say so in the report as missing evidence; do not treat silence as a
pass.

The preflight gate is not a test run for every kind of change. Find out what it
actually executes in this repo before treating it as coverage (in the ascent
repo, `make preflight-fast` runs no Django tests at all). For every test module
the diff adds or changes, and for the tests that cover each source file the
diff changes, run them yourself in the implementation worktree with the repo's
own test command (for example `make test ARGS='<module> <module>'`) and record
the exact command, the tally and the exit code in the report. A test the diff
itself adds or edits that you have not seen pass is missing evidence: the
verdict cannot be `approved`. After a merge of the default branch into the
work branch, run them again on the merged commit: a test can pass on the
branch and fail once the default branch's changes arrive (gc-4kgy1: a review
approved, and publish pushed, a commit whose own new test had been failing
since the merge).

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
