This is the `build-from-review-base` decide-repair stage: it runs once the fix
loop that `repair-review` launched has closed, reads the loop's final
re-review, and records the repair outcome on the workflow root — approved,
exhausted or blocked — or launches the next attempt.

Read review_mode={{review_mode}}, interaction_mode={{interaction_mode}},
review_fix_formula={{review_fix_formula}}, implementation_target={{implementation_target}},
implementation_formula={{implementation_formula}}, implementation_item_formula={{implementation_item_formula}},
and max_iterations={{max_iterations}} from the workflow root metadata and
artifacts.

Start with the repair-owner attempt journal,
`{{artifact_root}}/repair-journal*.md` (`repair-journal.md`, or
`repair-journal-attempt-<n>.md` per attempt), BEFORE the latest review or
summary. It names every loop launched so far (root bead id, attempt number,
launch time) and every wait already explained (gc-ndjvd). Resolve a relative
artifact root against the launcher rig root in `gc.work_dir`. Then read the
workflow root.

If `gc.build.repair_status` on the workflow root is already terminal, nothing
is left to decide: `not_needed` (the review was approved and no loop ran)
closes this step with `gc.outcome=pass`; `repairable` or `blocked` (recorded
by `repair-review` with its restart metadata, which you keep exactly as
recorded) closes it with `gc.outcome=fail`. Do not launch anything.

Otherwise `gc.build.fix_loop_root_id` names the loop this step was blocked on.
Confirm with `gc bd show "<loop-root-id>"` that it is closed. The engine
makes this step ready only when every bead it depends on is closed, so an
open loop root here means the dependency was never added or was removed, or
this step was dispatched by hand. Do not poll it. Park this session on the
loop durably and stop working:

```bash
SESSION_TARGET="${GC_SESSION_ID:-${GC_SESSION_NAME:-}}"
WAIT_NOTE="decide-repair <claimed-step-id>: fix loop <loop-root-id> closed; re-read {{artifact_root}}/repair-journal.md and decide the repair outcome."
if [ -n "$SESSION_TARGET" ] && ! gc wait list --session "$SESSION_TARGET" | grep -Fq "$WAIT_NOTE"; then
  gc session wait "$SESSION_TARGET" \
    --sleep \
    --on-beads "<loop-root-id>" \
    --note "$WAIT_NOTE"
fi
```

The controller drains the session to sleep and wakes it with that note when
the loop root closes; the woken session starts again from the journal. Note
the wait in the journal. A loop root's own `gc.outcome=pass` means only that
the loop ran to its end; the verdict is in the re-review artifact, as the
next paragraph requires.

The exit reason is whichever of the three happened (approved, blocked or
exhausted), determined by re-reading the
MOST RECENT re-review artifact's own verdict — never by whether the
attempt counter reached max_iterations. A final
attempt that is itself approved is `repair_status=approved`, full stop, even
when it also happens to be the last allowed attempt: hitting the iteration
ceiling on the same attempt that passed is not the same as exhausting the
ceiling without passing, and must never be recorded as blocked or exhausted
(confirmed incident, gc-jxl5x: workflow root gcas-gigyfp closed
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
change; required gates (e.g. preflight) were run with exit codes recorded. A
re-review that approved a pass without that record is missing evidence: treat
its verdict as `changes_required` for the next attempt and say so in the
journal.

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

**The next attempt.** When the most recent re-review's verdict is
`changes_required` (and not wording-only, above) and the attempts made so far
— the loops the journal records — are fewer than max_iterations, launch the
next loop exactly as `repair-review` does: adopt a loop
`gc.build.fix_loop_root_id` names if it is still open; otherwise sling
`{{review_fix_formula}}` to this step bead's own `gc.routed_to` with the same
`--var` set (`findings_path` = the most recent re-review report,
`context_path`, `implementation_formula`, `implementation_target`,
`code_review_formula`, `max_iterations` = {{max_iterations}} minus the
attempts already made, `push={{push}}`, `open_pr={{open_pr}}`), never with
`--on` (gc-bkybl). The exact block is in `repair-review`'s step 2, shown here
so the two launches cannot drift:

```bash
gc sling "<routed-to>" {{review_fix_formula}} --formula \
  --var "findings_path=<most recent review or re-review report path>" \
  --var "context_path=<implementation summary path, when one is recorded>" \
  --var "implementation_formula={{implementation_formula}}" \
  --var "implementation_target={{implementation_target}}" \
  --var "code_review_formula={{code_review_formula}}" \
  --var "max_iterations=<{{max_iterations}} minus fix attempts already made>" \
  --var "push={{push}}" \
  --var "open_pr={{open_pr}}"
```

Immediately record the new root id as `gc.build.fix_loop_root_id` (replacing
the closed loop's), keep `gc.build.repair_status=running`, append the attempt
to the journal, and then park this session on the new loop root with the
`gc session wait --sleep --on-beads` block above (a new note naming the new
root). Keep this step claimed; the session sleeps, and the controller wakes
it with the note when that loop closes. Nothing in this step polls a loop:
a claimed step that only waits holds a run-operator slot another build needs
(gc-2n9i3). When the ceiling is reached without an approved re-review, the
outcome is `exhausted` below; the same attempt that is approved is
`approved`, whatever the counter says.

Record every attempt on the workflow root metadata and in artifacts. Every
time you write `gc.build.repair_status`, also refresh `gc.build.review_verdict`,
`gc.build.review_report_path`, and `gc.build.review_subject_commit` on the
workflow root to the MOST RECENT review/re-review artifact's own verdict,
path, and reviewed commit — never leave the original (pre-repair) review
step's verdict, path, or commit in place once a repair loop has run;
finalize and the publish step both read these directly and will trust a
stale value over the true latest artifact. `gc.build.review_subject_commit`
must be the commit that the MOST RECENT approving re-review actually
reviewed, resolved fresh at the moment you record it: the commit that
re-review's report names or, if it names none, the pull request head
(`gh pr view <number> --json headRefOid --jq .headRefOid`; with no pull
request, `git rev-parse <branch>` at the launcher rig root). Do not look for
it with `git rev-parse HEAD` in a worktree: the implementation worktree
belongs to a closed bead and the re-reviewer removes its own worktree, so
both may be gone by now (gc-mpaqx). Never carry it forward from an earlier
attempt or an unrelated continuation (gc-ajt3i: a publish step opened a PR 3
approved commits behind because this value was never refreshed after a repair
loop). On approval,
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
"<claimed-step-id>" --reason "<concise reason>"`. The bounded repair loop
completing its attempts without an internal error is not the same as
approval — do not leave this step's own claimed bead at a default/pass
outcome while recording blocked/exhausted on the root.

If the loop's re-review artifact, the implementation evidence, or the
selected formula is missing, do not invent a pass. Read the workflow root
first. If it already carries a `gc.failure_class` or a
`gc.restart.entrypoint`, an earlier step stopped the build and recorded the
cause (`gc.build.blocked_step` names it when a gate did). Keep that
`gc.failure_class`, `gc.restart.entrypoint`, and `gc.restart.reason` exactly
as recorded (gc-2ua7i); record only `gc.build.repair_status=blocked` and
close this step's own claimed bead with `gc.outcome=fail`. Otherwise record
`gc.build.repair_status=blocked`, `gc.build.status=blocked`,
`gc.build.blocked_step=decide-repair`, `gc.outcome=fail`,
`gc.failure_class=review_repair_blocked`,
`gc.restart.entrypoint=build-from-review`, and `gc.restart.reason` with the
machine-readable blocked reason. `review_repair_blocked` means a prerequisite
was missing, nothing else — an exhausted repair loop uses
`review_repair_exhausted` above instead, even though both are terminal
failures.

Do not close the workflow root with `gc.outcome=pass` from this stage.

## Closing this step

Every stop this step records on the workflow root (`exhausted` or `blocked`)
also records `gc.build.status=blocked` and
`gc.build.blocked_step=decide-repair` next to its `gc.failure_class` and
`gc.restart.*` values. Finalize keeps a recorded stop and derives one only
when none is recorded.

Always set `gc.outcome` on this step's own claimed bead before closing it:

- `gc.build.repair_status` is `not_needed` or `approved`:
  `gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then
  `gc bd close "<claimed-step-id>" --reason "<concise reason>"`.
- Any other `gc.build.repair_status`:
  `gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then
  `gc bd close "<claimed-step-id>" --reason "<concise reason>"`.

A bead closed with no `gc.outcome` does not fail the build by itself: finalize
and publish then decide from the workflow root metadata alone. Never rely on
that for a blocked or exhausted result, and never close this step while a
loop it launched is still open: a `running` repair status at finalize is
read as blocked.
