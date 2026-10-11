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

Hand the fix to re-review with green CI (gc-68exu). The launcher passes the
build's publishing intent as push {{push}} and open_pr {{open_pr}}. When both
are `true`, or whenever the branch under repair already has an open pull
request, this step does not end at the fix commit. It ends when the fix is on
GitHub and every check on it is green, so the re-review never starts on a
commit whose result is red or unknown:

1. Push the fix commits to the branch under repair on `origin`, fast-forward
   only. Let the repo's pre-push hooks run; never bypass them. If the branch
   has no open pull request and push and open_pr are both `true`, open one as
   a **draft** against the default branch with the source bead id in the
   title. Leave a draft a draft: marking it ready belongs exclusively to the
   publish step.
2. Wait for GitHub's checks on the pushed commit, on the exact run, with a
   bound. Resolve the run ids for the head you pushed:
   `gh run list --commit "$(git rev-parse HEAD)" --json databaseId,name,status --jq '.[] | "\(.databaseId) \(.name) \(.status)"'`
   (nothing listed means CI has not registered yet: wait a minute and run it
   again), then watch each run by id:
   `perl -e '$t=shift; $p=fork; exec @ARGV unless $p; $SIG{ALRM}=sub{kill "TERM",$p; exit 124}; alarm $t; waitpid $p,0; exit($? & 127 ? 128+($? & 127) : $?>>8)' 1500 gh run watch <run-id> --exit-status`.
   This `perl` wrapper is the time limit that works on macOS and Linux alike
   (GNU `timeout` is not installed on macOS, and a bare `alarm` before `exec`
   does not stop `gh`, which ignores that signal). `gh run watch` returns the
   moment that run finishes (0: green, 1: a job failed), not at the next tick
   of a 30-second poll over every check on the pull request, which is how a
   fix worker once sat 25-30 minutes past the end of a run (gc-3mkjc); the
   wrapper ends it after 25 minutes (status 124), so it is not a blocking
   command of the kind the shell rules forbid. Any other status (128 or more:
   `gh` was ended by a signal) means the wait did not finish: run it again.
   If your shell tool allows less than that for one call, pass a smaller
   number of seconds, or run it in the background and wait for it to exit. Do
   not replace it with a fixed sleep or a long poll interval. When every run
   for the head has finished, run `gh pr checks <number>` once, for statuses
   from other apps and anything still unfinished. A check that is running
   gets another bounded wait, three waits at most. A check that sat `queued`
   or `waiting` through a whole wait is stuck: do not wait again, take the
   exit under "If CI cannot be made green" below.
3. A failed check is yours to fix, in this pass, when the branch under repair
   broke it. Read the failed job's log (`gh run view <run-id> --log-failed`)
   and fix the cause. Before pushing, run the failed job's own command
   locally and see it pass: the shard it ran (`npx vitest --shard=<n>/<m>`,
   the matrix entry, the exact `make` target), copied from the job log, not
   the whole suite; a push that was not run locally first sends the wait
   around once more (gc-3mkjc: about an hour idle across two pushes). Then
   commit, push, and wait again. A failing test is fixed; it is
   never skipped, quarantined or re-baselined away. A failure the base branch
   also has is different: when the same job of the same workflow is red on the
   base branch's own most recent run of it, at the same step, and the base
   branch does not require that check, the gate lets it pass and prints a
   `WARNING` line naming both runs and the step. A job that fails at a step
   where the base branch's job passed is yours, whatever else is red on the
   base branch. A red check from another app (a deployment preview such as
   Vercel) that the base branch does not require passes with a `WARNING` as
   well. Do not fix the base branch's failure on this branch, and do not pass
   over it in silence: copy each `WARNING` line, with its URLs, into the repair
   summary (`## Repair`, below). The gate makes this call, not you: a red check that the base
   branch requires, that is green on the base branch, or that the base branch
   has never run, blocks. If the gate says the pull request has merge
   conflicts, merge the base branch into the branch under repair, resolve
   them, push, and wait again: GitHub runs no pull request checks on a
   conflicted pull request. If the failure is in CI's own machinery (a runner
   that died, a download that timed out), you may rerun the failed job once
   (`gh run rerun <run-id> --failed`) and must record that you did, with the
   run URL and both results, in the repair summary. The gate does not tell a
   flake from a defect: a check that is still red is red.
4. After the last push, record the commit you are handing to re-review on this
   loop's workflow root (resolve it from `gc.root_bead_id` on this step bead):
   `gc bd update "<loop-root-id>" --set-metadata "gc.build.handoff_commit=$(git rev-parse HEAD)" --set-metadata "gc.build.handoff_branch=<branch>"`.
5. Run the gate yourself from the launcher rig root, the same script the
   controller runs when this step closes:
   `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/pr-ci-green.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`.
   It must print `PASS` for the commit you recorded. Put the pull request URL,
   the head sha and that `PASS` line in the repair summary's `## Repair`.

If CI cannot be made green: when a check your branch broke is still red after
your fixes, a check is stuck, or the fix needs a decision that is not yours,
do not close the step as passed, do not weaken, skip or re-baseline a test,
and do not close it over and over to use up the attempts. Write what you found
in the repair summary (below) with `status: blocked`, then close the step as a
hard failure that names each
failing or stuck check with its URL:
`gc bd update "<claimed-step-id>" --set-metadata gc.outcome=fail --set-metadata gc.failure_class=hard --set-metadata "gc.failure_reason=<check names and URLs>"`,
then `gc bd close "<claimed-step-id>"`. `gc.failure_class=hard` ends the step
at once and the re-review does not start; a failed outcome without it is handed back
as a new attempt, up to three times.

When push or open_pr is not `true` and the branch has no open pull request,
skip this section; the gate records `skipped: no publishing intent` and passes.

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
`PASS` (step 5), or before the hard-failure close when CI cannot be made
green, as a `gc.build.repair-summary.v1` artifact in exactly this shape:

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

- Commit: `<sha>` on `<branch>`, pushed to `origin` (or: not pushed, no publishing intent)
- Pull request: <URL>, draft (or: none)
- CI: the gate's `PASS` line verbatim, every `WARNING` line with its URLs, and any `gh run rerun` with the run URL and both results

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

`status` is `fixed` when the commit above is on GitHub with green checks (or
the loop does not publish and the proofs pass), `blocked` on the hard-failure
exit below. The `## Coverage` table MUST MATCH `trace.coverage` row for row:
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

Artifact validation: the controller gates this step with `../assets/scripts/checks/pr-ci-green.sh` (CI, above), so no gate validates the repair summary for you; validate it yourself against schema `gc.build.repair-summary.v1` with the command line below before `gc.build.repair_summary_path` is recorded and the step closes, and fix every `error:` line it prints. The schema id of this artifact is `gc.build.repair-summary.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.repair-summary.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).

This step is gated by `../assets/scripts/checks/pr-ci-green.sh`. The
controller does not wait for CI. A step closed while a check is unfinished or
red, with a recorded commit that is not the pull request head, or while the
pull request is marked ready instead of draft (`gh pr ready --undo <number>`
makes it a draft again), fails the gate and comes back to you as a new attempt
with the failing checks in `gc.attempt_log` on the gate's control bead (the
dependent of this step bead); after three attempts the step fails and the
re-review does not start. GitHub or the bead store being unreachable is not an
attempt: the gate retries, and says `INFRA` rather than `FAIL`.

Before closing this step, run `git checkout --detach` in your own directory so
the branch is not left held by your slot for the next session that needs it.
