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
2. Wait for GitHub's checks on the pushed commit, with a bound:
   `perl -e '$t=shift; $p=fork; exec @ARGV unless $p; $SIG{ALRM}=sub{kill "TERM",$p; exit 124}; alarm $t; waitpid $p,0; exit($? & 127 ? 128+($? & 127) : $?>>8)' 1500 gh pr checks <number> --watch --interval 30`.
   This `perl` wrapper is the time limit that works on macOS and Linux alike
   (GNU `timeout` is not installed on macOS, and a bare `alarm` before `exec`
   does not stop `gh`, which ignores that signal). The command returns by
   itself when the checks finish (0: green, 1: a check failed) and the wrapper
   ends it after 25 minutes (status 124), so it is not a blocking command of
   the kind the shell rules forbid. Any other status (128 or more: `gh` was
   ended by a signal) means the wait did not finish: run it again. If your shell tool allows less than that for one
   call, pass a smaller number of seconds, or run it in the background and
   wait for it to exit. Do not replace it with a fixed sleep or a long poll
   interval: a fix worker once sat an hour past the end of a run that way. If it
   says no checks are reported yet, CI has not registered; wait a minute and
   run it again. When the wrapper ends the wait, run `gh pr checks <number>`
   once and read what is unfinished. A check that is running gets another
   bounded wait, three waits at most. A check that sat `queued` or `waiting`
   through a whole wait is stuck: do not wait again, take the exit under "If
   CI cannot be made green" below.
3. A failed check is yours to fix, in this pass, when the branch under repair
   broke it. Read the failed job's log (`gh run view <run-id> --log-failed`),
   fix the cause, commit, push, and wait again. A failing test is fixed; it is
   never skipped, quarantined or re-baselined away. A failure the base branch
   also has is different: when the same job of the same workflow is red on the
   base branch's own most recent run of it, at the same step, and the base
   branch does not require that check, the gate lets it pass and prints a
   `WARNING` line naming both runs and the step. A job that fails at a step
   where the base branch's job passed is yours, whatever else is red on the
   base branch. A red check from another app (a deployment preview such as
   Vercel) that the base branch does not require passes with a `WARNING` as
   well. Do not fix the base branch's failure on this branch, and do not pass
   over it in silence: copy each `WARNING` line, with its URLs, into the fix
   summary. The gate makes this call, not you: a red check that the base
   branch requires, that is green on the base branch, or that the base branch
   has never run, blocks. If the gate says the pull request has merge
   conflicts, merge the base branch into the branch under repair, resolve
   them, push, and wait again: GitHub runs no pull request checks on a
   conflicted pull request. If the failure is in CI's own machinery (a runner
   that died, a download that timed out), you may rerun the failed job once
   (`gh run rerun <run-id> --failed`) and must record that you did, with the
   run URL and both results, in the fix summary. The gate does not tell a
   flake from a defect: a check that is still red is red.
4. After the last push, record the commit you are handing to re-review on this
   loop's workflow root (resolve it from `gc.root_bead_id` on this step bead):
   `gc bd update "<loop-root-id>" --set-metadata "gc.build.handoff_commit=$(git rev-parse HEAD)" --set-metadata "gc.build.handoff_branch=<branch>"`.
5. Run the gate yourself from the launcher rig root, the same script the
   controller runs when this step closes:
   `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/pr-ci-green.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`.
   It must print `PASS` for the commit you recorded. Put the pull request URL,
   the head sha and that `PASS` line in the fix summary.

If CI cannot be made green: when a check your branch broke is still red after
your fixes, a check is stuck, or the fix needs a decision that is not yours,
do not close the step as passed, do not weaken, skip or re-baseline a test,
and do not close it over and over to use up the attempts. Write what you found
in the fix summary, then close the step as a hard failure that names each
failing or stuck check with its URL:
`gc bd update "<claimed-step-id>" --set-metadata gc.outcome=fail --set-metadata gc.failure_class=hard --set-metadata "gc.failure_reason=<check names and URLs>"`,
then `gc bd close "<claimed-step-id>"`. `gc.failure_class=hard` ends the step
at once and the re-review does not start; a failed outcome without it is handed back
as a new attempt, up to three times.

When push or open_pr is not `true` and the branch has no open pull request,
skip this section; the gate records `skipped: no publishing intent` and passes.

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
