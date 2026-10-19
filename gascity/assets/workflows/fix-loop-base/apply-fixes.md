This is the `fix-loop-base` methodology contract fix-application step.

Concrete methodology packs override this step to run fixes through
`{{implementation_formula}}` with implementation target
`{{implementation_target}}`. Keep the fix work scoped to the findings artifact.

Work only in your own session's working directory. The branch under repair is
often still checked out in the worktree of the session that last worked on
it, and `git checkout <branch>` then fails with `'<branch>' is already used by
worktree at '<path>'`. Never `cd` into that path to work there: it is another
session's slot, and the next session started in it will switch branches under
your running commands (gc-qsbs9: a 5,630-test run was killed this way and the
uncommitted fix was stashed away). Instead:

- If `git -C "<path>" status --porcelain` prints nothing, free the branch with
  `git -C "<path>" checkout --detach` (this changes no file there), then check
  the branch out in your own directory.
- If it prints anything, that worktree holds uncommitted work on this branch.
  Do not touch it. Close this step as a hard failure and name the path:
  `gc bd update "<claimed-step-id>" --set-metadata gc.outcome=fail --set-metadata gc.failure_class=hard --set-metadata "gc.failure_reason=uncommitted work on <branch> in <path>"`,
  then `gc bd close "<claimed-step-id>"`. Without `gc.failure_class=hard` the
  step is handed back as a new attempt, up to three times, and each one finds
  the same worktree.

Your first git step, before any edit or commit, is to fast-forward to origin:
`git fetch origin <branch> && git merge --ff-only origin/<branch>`. The publish
step may have added an empty `chore: run CI` commit to `origin/<branch>`
(reviewer-first, gc-gef2p8) after the last hand-off, and a fix committed
without it would make your checkpoint push non-fast-forward. When the
fast-forward is refused (your branch has commits `origin/<branch>` lacks and
the other way round, so the two have diverged), that is a finding, not
something to force: never `git push --force`, never `git reset --hard
origin/<branch>`, and never rebase away either side. Write it in the repair
summary with `status: blocked` and close the step as the hard failure below,
naming both heads. When the branch does not exist on origin (a workflow
that does not publish never pushed it), there is nothing to fast-forward to:
continue.

Hand the fix to re-review by local commit (reviewer-first, gc-gef2p8). The
launcher passes the build's publishing intent as push {{push}} and open_pr
{{open_pr}}. CI does not run between a fix pass and its re-review: the
re-reviewer works from your local commit, in a scratch worktree of the same
repository, and CI runs once, on the approved commit, in the publish step.
When both are `true`, this step ends with a commit, a recorded hand-off and one
checkpoint push. It opens no pull request and waits for no CI:

1. Fix the findings. Before the commit, run the local pass the fix plan lists:
   `make preflight-fast` (or the rig's documented fast gate) and the tests of
   the code you changed. A failing test is fixed; it is never skipped,
   quarantined or re-baselined away. Do not run the full browser suite or the
   real-server suites: the re-reviewer runs them once, on every device
   profile.
2. Commit on the branch under repair. The subject of the fix commit (the head
   of the branch) carries the tag `[skip ci]`, for example
   `fix: keep row taps on phones [skip ci]`. GitHub skips the push and
   pull_request workflows for a head commit with that tag, and the required
   checks stay pending, so nothing can merge the branch before the publish
   step.
3. Record the commit you are handing to re-review on this loop's workflow root
   (resolve it from `gc.root_bead_id` on this step bead):
   `gc bd update "<loop-root-id>" --set-metadata "gc.build.handoff_commit=$(git rev-parse HEAD)" --set-metadata "gc.build.handoff_branch=<branch>"`.
4. Make exactly one checkpoint push, for durability: `git push origin
   <branch>`, fast-forward only. Let the repo's pre-push hooks run; never
   bypass them. Do not run `gh pr create` and do not touch an existing pull
   request: opening it, waiting for its checks and marking it ready belong
   exclusively to the publish step.
5. Run the gate yourself from the launcher rig root, the same script the
   controller runs when this step closes:
   `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/handoff-commit-ancestor.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`.
   It must print `PASS` for the commit you recorded: that commit exists in the
   rig repository and is an ancestor of the branch head. Put the commit, the
   branch, that `PASS` line and the local pass (each command with its exit
   code) in the repair summary's `## Repair`.

If the fix cannot be made, or the local pass cannot be made green, or the fix
needs a decision that is not yours, do not close the step as passed, do not
weaken, skip or re-baseline a test, and do not close it over and over to use up
the attempts. Write what you found in the repair summary (below) with
`status: blocked`, then close the step as a hard failure that names each
failing command:
`gc bd update "<claimed-step-id>" --set-metadata gc.outcome=fail --set-metadata gc.failure_class=hard --set-metadata "gc.failure_reason=<commands and results>"`,
then `gc bd close "<claimed-step-id>"`. `gc.failure_class=hard` ends the step
at once and the re-review does not start: this step and the re-review are
members of the loop's `fix-body` scope, so the engine skips the re-review and
closes the loop root `gc.outcome=fail`. Use `hard` when the local pass cannot be
made green, unless the failure reason names an environment blocker (Docker API
not answering, disk full, a shared service down): then `decide-repair`'s probe
block governs and may relaunch after the probe passes. The root does not copy
`gc.failure_class` or `gc.failure_reason`: they stay on this step's bead, which
`decide-repair` reads.

An explicit `gc.outcome=fail` is terminal from attempt 2 on, whatever its
failure class. The engine strips the scope keys (`gc.scope_ref`,
`gc.scope_role`, `gc.on_fail`) from attempt 1 only, and copies them onto
attempt 2 and later (`internal/formula/ralph.go:109-111`,
`internal/dispatch/control.go:864-869`), so a `gc.outcome=fail` close on
attempt 2 or 3 aborts the scope, skips the re-review and fails the loop root,
and the remaining attempt is lost. To ask for a retry, close the step WITHOUT
`gc.outcome`: the check gate then drives a new attempt, up to three in all.
`hard` stays terminal at any attempt once engine fix `3c8cd61ae` is installed.

When push or open_pr is not `true`, make no checkpoint push; the gate then
records `skipped: no publishing intent` when no hand-off is recorded, and still
checks a recorded one.

## The repair summary

Every pass of this step, fixed or blocked, ends with one artifact: the repair
summary, `repair-summary-<claimed-step-id>.md` in the build's artifact root,
which is the directory of `{{findings_path}}` (the review report this loop
fixes; a relative path resolves against the launcher rig root in `gc.work_dir`
on the loop root). It is the implementation self-check record that
`decide-repair` reads before it approves the repair. Until 2026-10-11 this
step asked for a "fix summary" and named no file, so a pass recorded its V1-V7
proofs only in its close reason, and decide-repair launched a second,
evidence-only fix loop after an APPROVED re-review just to produce the record
(gc-0d4124: loop gcas-oopady, about an hour of factory time). A close reason is
not the record; this file is. Write it after the last push and the gate's
`PASS` (step 5), or before the hard-failure close when the fix cannot be made, as a `gc.build.repair-summary.v1` artifact in exactly this shape:

```markdown
---
schema: gc.build.repair-summary.v1
workflow:
  id: <loop-root-id>
  formula: fix-loop-base
methodology:
  pack: gascity
  name: fix-loop-base
producer:
  formula: fix-loop-base
  stage: apply-fixes
  attempt: <gc.attempt of this step bead; 1 on the first attempt>
repair:
  commit: <git rev-parse HEAD after the last push, the value of gc.build.handoff_commit; none when blocked before a commit>
  branch: <branch under repair>
status: fixed
trace:
  upstream:
    - path: <absolute path of {{findings_path}}>
      hash: sha256:<shasum -a 256 of that file>
      ids: [F1, F2, F3]
  coverage:
    - {id: F1, status: covered}
    - {id: F2, status: covered}
    - {id: F3, status: blocked, rationale: <one line: what is missing and who owns it>}
---

## Repair

- Commit: `<sha>` on `<branch>`, checkpoint-pushed to `origin` with `[skip ci]` (or: not pushed, no publishing intent)
- Pull request: none (reviewer-first: the publish step opens it)
- Gate: the `handoff-commit-ancestor.sh` `PASS` line verbatim, and the local pass with each command's exit code

## Findings Addressed

One entry per finding id in the findings report: what changed and where (file:line), and the class of defect it closes, from the fix plan.

## Coverage

| id | status |
| --- | --- |
| F1 | covered |
| F2 | covered |
| F3 | blocked |

## Self-Check

| id | command | exit | result |
| --- | --- | --- | --- |
| V1 | `make test ARGS='reports accounts'` | 0 | 1684 passed in 135 files |
| V2 | `make preflight-fast` | 0 | clean |

- every check the fix plan lists is a row above, with the exit code of the command under test (never `tail`'s or `grep`'s after a pipe: run it alone, or `set -o pipefail`)
- every commit sha cited in this file passes `git merge-base --is-ancestor <sha> HEAD`
- the metadata above (status, coverage, repair.commit) matches this text
- regenerated baselines/snapshots were opened and show the intended change (name them), or none were regenerated
- every required gate the plan or repo names (e.g. preflight) ran, with its exit code above

## Changed Files

- `<path>`: <what changed>
```

`status` is `fixed` when the commit above is recorded as the hand-off, passed
the gate and the local pass is green (CI runs later, at publish), `blocked` on
the hard-failure exit below. The `## Coverage` table MUST MATCH `trace.coverage` row for row:
the validator pairs the two and fails with `markdown coverage matrix is
missing` or a mismatch otherwise, which is why the `## Self-Check` table has
no `status` column. `trace.upstream[].ids` lists every finding id the findings
report defines, and every one has a coverage row; a finding this pass
deliberately leaves (`blocked`, `deferred`, `out_of_scope`) carries a one-line
`rationale`.

Validate it, then record its absolute path on the loop root (resolve the root
from `gc.root_bead_id` on this step bead), in this order, before closing:

```bash
python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.repair-summary.v1 --path <absolute path of repair-summary-<claimed-step-id>.md>
gc bd update "<loop-root-id>" --set-metadata "gc.build.repair_summary_path=<absolute path of repair-summary-<claimed-step-id>.md>"
```

Last thing before `gc bd close`:
`gc bd show "<loop-root-id>" --json | jq -r '(if type == "array" then .[0] else . end).metadata["gc.build.repair_summary_path"]'`
must print the path. `decide-repair` reads exactly that key; a close reason, a
mail, or a file it is not told about does not reach it.

Artifact validation: the controller gates this step with `../assets/scripts/checks/handoff-commit-ancestor.sh` (the hand-off, above), so no gate validates the repair summary for you; validate it yourself against schema `gc.build.repair-summary.v1` with the command line below before `gc.build.repair_summary_path` is recorded and the step closes, and fix every `error:` line it prints. The schema id of this artifact is `gc.build.repair-summary.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.repair-summary.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).

This step is gated by `../assets/scripts/checks/handoff-commit-ancestor.sh`.
The controller does not wait for CI and there is none to wait for. A step
closed with no `gc.build.handoff_commit` recorded in a workflow that publishes,
or with a recorded commit that is not in the rig repository or not an ancestor
of the head of the recorded branch, fails the gate and comes back to you as a
new attempt with the reason in `gc.attempt_log` on the gate's control bead (the
dependent of this step bead); after three attempts the step fails and the
re-review does not start. The bead store being unreachable is not an attempt:
the gate says `INFRA` rather than `FAIL`.

Before closing this step, run `git checkout --detach` in your own directory so
the branch is not left held by your slot for the next session that needs it.
