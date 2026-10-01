This is the `build-from-review-base` review stage.

Review the implementation against:

- requirements_path: {{requirements_path}}
- plan_path: {{plan_path}}
- plan_review_path: {{plan_review_path}}
- decomposition_path: {{decomposition_path}}
- implementation_summary_path: {{implementation_summary_path}}
- code_review_formula: {{code_review_formula}}
- review_fix_formula: {{review_fix_formula}}
- implementation_formula: {{implementation_formula}}
- implementation_item_formula: {{implementation_item_formula}}
- review_mode: {{review_mode}}
- interaction_mode: {{interaction_mode}}
- max_iterations: {{max_iterations}}

Use the selected code review methodology to produce a review verdict and
findings. This stage records the review result; the following `repair-review`
stage owns any selected review-fix loop, restart handoff, or blocked repair
state.

As part of this review, actually run the rig's full local-CI-equivalent gate
yourself in the implementation worktree (not the launcher checkout) — `make
preflight-fast` if the worktree's Makefile defines that target, otherwise
`make preflight` — and record the exact command and its outcome. Do not
accept or forward a prose claim about preflight from the implementation stage
as a substitute for running it here: that was tried and verified live to be
attention-dependent, not guaranteed (one re-review caught a missing run, an
identical re-review of a different item did not). A failing run is a required
fix (`changes_required`), not missing evidence.

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

Verify load-bearing facts before any wording concern: the SHAs, what actually
failed, and the failing code's content at the failure SHA. Accept a fix for an
intermittent or timing failure only with evidence that the pre-fix code fails
and the post-fix code passes under the same reproduction method (throttling,
repeats, a stress harness; the repo's AGENTS.md names it). If the pre-fix code
cannot be made to fail, the verdict is `blocked` with reason `cannot_reproduce`
and the evidence gathered recorded: never `approved`, never a skip. Never
demand evidence the repo cannot produce.

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

For `review_mode=report`, write findings and verdicts without mutating code.
For `review_mode=agent`, write a structured fix handoff for the caller or
selected fix loop. For `review_mode=interactive`, safe fixes may be negotiated
or applied, but every change and reason must be recorded.

Close this step only when the implementation has a concrete review verdict:
`approved`, `changes_required`, or `blocked`. Record the review report path,
verdict, unresolved findings, drift observations, and any existing fix-attempt
count on the workflow root metadata.

When the verdict is `approved`, also record `gc.build.review_subject_commit`
on the workflow root, resolved fresh right now from the actual reviewed
worktree's `git rev-parse HEAD` — never copied from `implementation_summary_path`,
an earlier continuation's publish metadata, or any other prior run's value.
This is the only sha the publish step is allowed to treat as approved for
push/PR purposes (gc-ajt3i: a publish step trusted a stale pre-repair commit
here and opened a PR 3 approved commits behind).

Artifact validation: this stage is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.review_report_path` against schema `gc.build.review.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact.
