This is the `build-from-review-base` repair-review stage.

Read the review report, review verdict, unresolved findings, fix attempt count,
review_mode={{review_mode}}, interaction_mode={{interaction_mode}},
review_fix_formula={{review_fix_formula}}, implementation_target={{implementation_target}},
implementation_formula={{implementation_formula}}, implementation_item_formula={{implementation_item_formula}},
and max_iterations={{max_iterations}} from the workflow root metadata and
artifacts.

If the review verdict is approved, do not mutate code. Record
`gc.build.repair_status=not_needed`, preserve the approved review metadata, and
close this step successfully.

If the review verdict is `changes_required` and review_mode=report, do not
mutate code. Write a repair handoff artifact that names the selected
review_fix_formula, affected requirements, target files or work items when
known, current review report path, and the exact continuation entrypoint to
restart from. Record at least:

- `gc.build.repair_status=repairable`
- `gc.restart.entrypoint=build-from-review`
- `gc.restart.reason=review_changes_required`
- `gc.restart.review_report_path=<review report path>`
- `gc.restart.review_fix_formula={{review_fix_formula}}`
- `gc.restart.implementation_target={{implementation_target}}`

Then close this step with failure metadata so the workflow cannot finalize as a
pass without an explicit restart.

If the review verdict is `changes_required` and review_mode is agent or
interactive, run or dispatch the selected review_fix_formula against the
recorded review findings and implementation evidence until one of these happens:

- the implementation is approved;
- review or repair returns blocked;
- max_iterations is exhausted.

The exit reason is whichever of the three happened, determined by re-reading
the MOST RECENT re-review artifact's own verdict — never by whether the
attempt counter reached max_iterations. A final attempt that is itself
approved is `repair_status=approved`, full stop, even when it also happens to
be the last allowed attempt: hitting the iteration ceiling on the same
attempt that passed is not the same as exhausting the ceiling without
passing, and must never be recorded as blocked or exhausted (confirmed
incident, gc-jxl5x: workflow root gcas-gigyfp closed
`gc.outcome=fail`/`gc.failure_class=review_repair_blocked` although its
attempt-6 re-review report read `verdict: approved`, 0 unresolved findings --
the work had already merged as PR #2412). Before recording any
non-`approved` repair_status, re-open the artifact at the review report path
you are about to cite and confirm its own status/verdict field actually
supports that outcome.

Each repair pass that commits a fix runs the implementation self-check before
re-review and records it in the repair artifact. It fails closed: the checks
the plan lists were run (command and exit code); every cited commit sha passes
`git merge-base --is-ancestor <sha> HEAD`; summary metadata matches its own
text; regenerated baselines/snapshots were opened and show the intended
change; required gates (e.g. preflight) were run with exit codes recorded.

Wording-only findings touch only prose (docs, reply drafts, reports, code
comments) and change no code or test behaviour. Fix them in the same repair
pass as the code findings. When the most recent re-review's only remaining
findings are wording-only, fix them here instead of starting another full
review loop: re-read each result against its finding, and confirm
`git diff <reviewed-sha> HEAD` (the commit that re-review examined) changes
only prose. Then write a `gc.build.review.v1` wording-fix validation artifact
with `status: approved` that lists each finding and its fix and states that
the diff since `<reviewed-sha>` is wording-only. It counts as the most recent
re-review artifact for the rule above: record it as
`gc.build.review_report_path`, with `gc.build.review_verdict=approved` and the
fresh `git rev-parse HEAD` as `gc.build.review_subject_commit`, so publish
pushes the final HEAD. A diff that changes code or test behaviour always gets
a real re-review; this rule never skips one.

A repair for an intermittent or timing failure must show the pre-fix code
failing and the post-fix code passing under the same reproduction method. When
the review is `blocked` for `cannot_reproduce`, or a repair pass cannot make
the pre-fix code fail, do not guess-fix: record the evidence gathered in the
repair artifact and the blocked outcome below (a reproduction method is the
missing prerequisite), with `gc.restart.reason=cannot_reproduce`.

Record every attempt on the workflow root metadata and in artifacts. Every
time you write `gc.build.repair_status`, also refresh `gc.build.review_verdict`,
`gc.build.review_report_path`, and `gc.build.review_subject_commit` on the
workflow root to the MOST RECENT review/re-review artifact's own verdict,
path, and reviewed commit — never leave the original (pre-repair) review
step's verdict, path, or commit in place once a repair loop has run;
finalize and the publish step both read these directly and will trust a
stale value over the true latest artifact. `gc.build.review_subject_commit`
must be resolved fresh from `git rev-parse HEAD` in the worktree that the
MOST RECENT approving re-review actually reviewed, at the moment you record
it — never carried forward from an earlier attempt or an unrelated
continuation (gc-ajt3i: a publish step opened a PR 3 approved commits behind
because this value was never refreshed after a repair loop). On approval,
record `gc.build.repair_status=approved`, `gc.build.review_verdict=approved`,
the final review report path, and the final reviewed commit sha as
`gc.build.review_subject_commit`. On a genuine exhausted-without-approval ceiling,
record `gc.build.repair_status=exhausted`, `gc.outcome=fail`,
`gc.failure_class=review_repair_exhausted` — reserve `review_repair_failed`
for a repair or review invocation that itself returned an error, and never use
`review_repair_blocked` for either of these cases: that class is reserved
below for missing prerequisites only, and conflating it with an exhausted
ceiling is exactly the gc-jxl5x confusion above. On blocked or exhausted
attempts, also record restart metadata with
`gc.restart.entrypoint=build-from-review` on the workflow root — then also
close THIS step's own claimed bead with the same outcome: `gc bd update
"<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then `gc bd close
"<claimed-step-id>" --reason "<concise reason>"`. As with the report-mode path
above, the bounded repair loop completing its attempts without an internal
error is not the same as approval — do not leave this step's own claimed bead
at a default/pass outcome while recording blocked/exhausted on the root.

If any prerequisite review artifact, implementation evidence, or selected
formula is missing, do not invent a pass. Record `gc.build.repair_status=blocked`,
`gc.outcome=fail`, `gc.failure_class=review_repair_blocked`,
`gc.restart.entrypoint=build-from-review`, and `gc.restart.reason` with the
machine-readable blocked reason. `review_repair_blocked` means a prerequisite
was missing, nothing else — an exhausted repair loop uses
`review_repair_exhausted` above instead, even though both are terminal
failures.

Do not close the workflow root with `gc.outcome=pass` from this stage.
