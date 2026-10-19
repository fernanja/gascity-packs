This is the `build-from-review-base` repair-review stage: it decides whether a
fix loop is needed and, when it is, launches the loop and hands the wait to
the next step. It never waits for a loop itself.

Read the review report, review verdict, unresolved findings, fix attempt count,
review_mode={{review_mode}}, interaction_mode={{interaction_mode}},
review_fix_formula={{review_fix_formula}}, implementation_target={{implementation_target}},
implementation_formula={{implementation_formula}}, implementation_item_formula={{implementation_item_formula}},
and max_iterations={{max_iterations}} from the workflow root metadata and
artifacts.

Repair is two steps (gc-2n9i3). This step launches at most one fix loop as a
standalone workflow, blocks the next step, `decide-repair`, on that loop's
root bead with a native dependency, and closes. The engine makes
`decide-repair` ready the moment the loop root closes; that step reads the
loop's final re-review, records the repair outcome, and launches the next
attempt when one is allowed. Until 2026-10-10 this one step stayed claimed by
a run-operator for the whole loop, polling every few minutes (4.5 hours and
59 waiting records in one day; gcas-98fgdi, gcas-5txabn); a claimed step
that only waits holds a slot another build needs.

Resuming this stage (the step bead was already claimed, or the workflow root
already carries `gc.build.fix_loop_root_id`) starts with the repair-owner
attempt journal, `{{artifact_root}}/repair-journal*.md` (`repair-journal.md`,
or `repair-journal-attempt-<n>.md` per attempt), BEFORE the latest review or
summary and before reading any loop. The journal names the running loop's
root bead id, the step bead that waits on it, and every wait already
explained, so a resumed session picks up where the previous one stopped
instead of rediscovering it (gc-ndjvd: a resumed operator polled the repair
loops for 70 minutes before opening the journal that already documented the
dependency waits). Only then read the latest review or re-review artifact and
the summary. A resumed session whose loop is already running has one job
left: make sure `decide-repair` is blocked on that loop (step 4 below), then
close this step.

If the review verdict is approved, do not mutate code. Record the status on
the workflow root,
`gc bd update "<workflow-root-id>" --set-metadata "gc.build.repair_status=not_needed"`,
preserve the approved review metadata, and close this step successfully
(closing section below).

If the review verdict is `changes_required` and review_mode=report, do not
mutate code. Write a repair handoff artifact that names the selected
review_fix_formula, affected requirements, target files or work items when
known, current review report path, and the exact continuation entrypoint to
restart from. Record at least:

- `gc.build.repair_status=repairable`
- `gc.build.status=blocked`
- `gc.build.blocked_step=repair-review`
- `gc.failure_class=review_changes_required`
- `gc.restart.entrypoint=build-from-review`
- `gc.restart.reason=review_changes_required`
- `gc.restart.review_report_path=<review report path>`
- `gc.restart.review_fix_formula={{review_fix_formula}}`
- `gc.restart.implementation_target={{implementation_target}}`

Then close this step with failure metadata so the workflow cannot finalize as a
pass without an explicit restart.

If the review verdict is `changes_required` and review_mode is agent or
interactive, launch the selected review_fix_formula against the recorded
review findings and implementation evidence. The loop runs until one of these
happens, and `decide-repair` reads which:

- the implementation is approved;
- review or repair returns blocked;
- max_iterations is exhausted.

One fix loop runs at a time per workflow root. This step launches the first
one and blocks `decide-repair` on it; `decide-repair` launches any later
attempt the same way, blocks its own step bead on the new root, and releases
its claim instead of waiting:

1. Before launching, read `gc.build.fix_loop_root_id` on the workflow root. If
   it names a bead that is still open or in_progress, adopt that loop: do not
   launch another, go to step 4. A session restarted mid-launch has no memory
   of the loop it launched, and this key is the only record of it (gc-75vzd: a
   restarted operator launched a second loop for the same findings 15 minutes
   after the first).
2. **Probe the environment before launching (gc-vth8v4).** A loop launched into
   an environment that is still broken burns three attempts and a run-operator
   claim on the same blocker (2026-10-18: new fix loops kept launching while the
   Docker API did not answer, every probe from 16:43Z on). Before the `gc sling`
   below, read what stopped the previous loop: the `gc.failure_reason` and
   close reason of the loop root and of its last step beads, the last
   re-review's blocked reason and `Permitted/environment` items, and the
   journal. At a first launch there is no previous loop: read the review
   report and the workflow root's `gc.failure_reason` instead. When any of
   them names an environment blocker, run its matching cheap probe from the
   launcher rig root, each bounded so it returns by itself:

   - Docker API not answering: `timeout 20 docker info >/dev/null 2>&1; echo "exit=$?"`
     (the rig's `scripts/docker_ready.sh`, when it has one, is the same probe);
   - host disk full: `df -Pk "$HOME" | awk 'NR==2 {print $4 / 1048576 " GiB free"}'`,
     failing under 10 GiB;
   - a shared service the loop did not start, named with its owner (for
     example the shared compose stack's port): `nc -z -w 5 <host> <port>; echo "exit=$?"`.
     Never probe a per-loop dev server: the worker that served it is gone, so
     nothing listens on a healthy host and every claim would defer.

   A passing probe changes nothing: launch. A failing probe means the blocker is
   still present, so do NOT launch and do not count an attempt (no loop ran).
   Defer instead, in this order:

   1. On the workflow root record the reason and the time:
      `gc bd update "<workflow-root-id>" --set-metadata "gc.build.repair_deferred=repair deferred: <blocker> still present at <UTC time from date -u>"`,
      and append the same line to the journal. Leave `gc.build.repair_status`
      as it is. Set `gc.build.repair_deferred_first_at` to the time too, in the
      same update, only when it is absent.
   2. Cap the deferral at 24 hours. When `gc.build.repair_deferred_first_at`
      is more than 24 hours old, stop deferring: set
      `gc.build.repair_status=blocked`, `gc.failure_class=review_repair_blocked`
      and `gc.restart.reason=environment` on the workflow root, record the
      blocker and how long it lasted in the journal, and close this step so
      finalize runs.
   3. Mail the mayor once per 6 hours, not once per claim: read
      `gc.build.repair_deferred_mailed_at` on the workflow root; send only when
      it is absent or more than 6 hours old, with
      `gc mail send mayor -s "repair deferred: <blocker>" -m "<root id>, probe command and exit code, time, deferring since <repair_deferred_first_at>"`,
      then set that key to the send time in the same `gc bd update` as step 1.
   4. Keep this step open and hide it from the queue for a while, so the next
      claim is a re-probe and not an immediate re-claim of the same blocker:
      `gc bd update "<claimed-step-id>" --defer +30m --status in_progress
      --set-metadata "gc.session_affinity=" --set-metadata "gc.continuation_group="`.
      `--status in_progress` is required: bd sets `status=deferred` for any
      future `--defer` date unless a status is given, and `release-if-current`
      only acts on an `in_progress` step, so a deferred step would stay
      assigned to this session forever. The release writes only status and
      assignee, so `defer_until` survives and the claim side honors it. Then
      run `gc bd release-if-current "<claimed-step-id>" "<assignee>"` (the
      assignee exactly as `gc bd show "<claimed-step-id>" --json` prints it;
      `skipped` means the bead was not yours, so stop and report). Do not close
      the step, do not park it with `gc session wait`, and do not poll.

   The claim after the deferral runs the probe again. When it passes, clear
   `gc.build.repair_deferred` and `gc.build.repair_deferred_first_at` with
   `--unset-metadata` and launch as below.

   An ordinary `changes_required` report that names no environment blocker
   needs no probe: launch at once.

   Then launch the loop as a standalone workflow, in exactly this shape:

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

   `push` and `open_pr` carry this build's publishing intent into the loop:
   with both `true`, each fix pass ends with a local fix commit recorded as
   `gc.build.handoff_commit` and one `[skip ci]` checkpoint push, and the
   re-review starts from that commit. No pull request is opened and no CI runs
   until the publish step (reviewer-first, gc-gef2p8).

   `<routed-to>` is this step bead's own `gc.routed_to` value. Never attach
   the loop to a bead with `--on` — not to this step's claimed bead, not to
   the `decide-repair` bead, and not to the source bead. When a workflow
   launched with `--on` finishes, the engine closes the bead it was attached
   to. Attached to a step of this build, that closes the step under its owner
   with a pass outcome and lets the build finalize while the next loop is
   still running (gc-bkybl: root gcas-6onr10 finalized blocked 10 minutes
   after its second fix loop started; the loop went on to be approved with
   nothing left to publish it).
3. Immediately after launch, record the new loop's root bead id as
   `gc.build.fix_loop_root_id` and `gc.build.repair_status=running` on the
   workflow root, and append to the journal
   `{{artifact_root}}/repair-journal.md`: the attempt number, the loop root
   id, the launch time, the step bead that waits on it (`decide-repair`, found
   in step 4), and every dependency or wait you are blocked on, as you learn
   it. Resolve a relative artifact root against the launcher rig root in
   `gc.work_dir`. The journal is the first thing a resuming operator reads
   (above), and `decide-repair` counts attempts from it.
4. Block `decide-repair` on the loop root. Find the `decide-repair` step bead
   by walking this step's dependents two hops:
   `gc bd dep list "<claimed-step-id>" --direction=up --json` lists the
   scope-check bead the engine inserted after this step
   (`metadata."gc.step_id"` ending in `repair-review-scope-check`);
   `gc bd dep list "<scope-check-id>" --direction=up --json` lists that
   bead's dependents, among them the `decide-repair` step bead
   (`gc.step_id` ending in `decide-repair`, title "Decide repair outcome").
   Never guess the id from a title search. Then run
   `gc bd dep add "<decide-repair-bead-id>" "<loop-root-id>"` and confirm with
   `gc bd dep list "<decide-repair-bead-id>"` that the loop root is listed
   `via blocks`. A workflow step is ready only when every bead it depends on
   is closed, so this one edge is what keeps `decide-repair` from running
   until the loop ends; the engine wires the same kind of edge for every
   `needs` in a formula. Add the edge BEFORE closing this step: the moment
   this step closes with no other open blocker, `decide-repair` is ready and
   a run-operator claims it against a loop that has barely started. Record
   the decide-repair bead id in the journal.
5. Do not poll the loop, do not wait for it, and do not read its re-review:
   none of that is this step's work. Close this step with `gc.outcome=pass`
   (closing section below). Its pass means "a loop is running and
   `decide-repair` is blocked on it", nothing about the repair's outcome,
   which `decide-repair` records.

Do not run the fix loop inside this session, and do not mutate code here.

If any prerequisite review artifact, implementation evidence, or selected
formula is missing, do not invent a pass. Read the workflow root first. If it
already carries a `gc.failure_class` or a `gc.restart.entrypoint`, an earlier
step stopped the build and recorded the cause (`gc.build.blocked_step` names
it when a gate did). Keep that `gc.failure_class`,
`gc.restart.entrypoint`, and `gc.restart.reason` exactly as recorded: the
missing prerequisite here is a consequence of that stop, and overwriting the
record sends the restart to the wrong entrypoint (gc-2ua7i: a build stopped by
a rejected plan ended with `gc.restart.entrypoint=build-from-review`). In that
case record only `gc.build.repair_status=blocked` and close this step's own
claimed bead with `gc.outcome=fail`.

Otherwise record `gc.build.repair_status=blocked`, `gc.build.status=blocked`,
`gc.build.blocked_step=repair-review`,
`gc.outcome=fail`, `gc.failure_class=review_repair_blocked`,
`gc.restart.entrypoint=build-from-review`, and `gc.restart.reason` with the
machine-readable blocked reason. `review_repair_blocked` means a prerequisite
was missing, nothing else — an exhausted repair loop is `decide-repair`'s
`review_repair_exhausted`, even though both are terminal failures.

Do not close the workflow root with `gc.outcome=pass` from this stage.

## Closing this step

Every stop this step records on the workflow root (report-mode `repairable`
or `blocked`) also records `gc.build.status=blocked` and
`gc.build.blocked_step=repair-review` next to its `gc.failure_class` and
`gc.restart.*` values. Finalize keeps a recorded stop and derives one only
when none is recorded. Then also close THIS step's own claimed bead with the
same outcome, as below.

Every `gc.build.repair_status` this step decides is written on the workflow
root (`gc.root_bead_id` on your claimed step bead) with
`gc bd update "<workflow-root-id>" --set-metadata "gc.build.repair_status=<value>"`
BEFORE the step closes, never only in a close reason, a mail or the journal:
no check script gates this step, and finalize waits on that key (gc-xhumy: a
step closed pass with "Repair not_needed" in its reason and nothing on the
root; finalize waited 25 minutes until the mayor wrote it). Last thing before
`gc bd close`:
`gc bd show "<workflow-root-id>" --json | jq -r '(if type == "array" then .[0] else . end).metadata["gc.build.repair_status"]'`
must print the value you decided.

Always set `gc.outcome` on this step's own claimed bead before closing it:

- `gc.build.repair_status` is `not_needed`, or `running` with
  `decide-repair` blocked on the loop root (step 4):
  `gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then
  `gc bd close "<claimed-step-id>" --reason "<concise reason>"`.
- Any other `gc.build.repair_status`:
  `gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then
  `gc bd close "<claimed-step-id>" --reason "<concise reason>"`.

A bead closed with no `gc.outcome` does not fail the build by itself: finalize
and publish then decide from the workflow root metadata alone. Never rely on
that for a blocked or repairable result.
