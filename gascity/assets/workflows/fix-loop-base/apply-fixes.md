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
  Do not touch it. Close this step with a failed outcome and name the path in
  the close reason.

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
2. Wait for GitHub's checks on the pushed commit: `gh pr checks <number> --watch`.
   The command returns by itself when the checks finish, so it is not a
   blocking command of the kind the shell rules forbid. If your shell tool
   limits how long one call may run, run it in the background and wait for it
   to exit, or run it again until it returns by itself. Do not replace it with
   a fixed sleep or a long poll interval: a fix worker once sat an hour past
   the end of a run that way. If it says no checks are reported yet, CI has
   not registered; wait a minute and run it again.
3. A failed check is yours to fix in this pass. Read the failed job's log
   (`gh run view <run-id> --log-failed`), fix the cause, commit, push, and
   wait again. A failing test is fixed; it is never skipped, quarantined or
   re-baselined away, and that includes a test that was already failing before
   this branch. If the failure is in CI's own machinery (a runner that died, a
   download that timed out), you may rerun the failed job once
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

When push or open_pr is not `true` and the branch has no open pull request,
skip this section; the gate records `skipped: no publishing intent` and passes.

This step is gated by `../assets/scripts/checks/pr-ci-green.sh`. The
controller does not wait for CI. A step closed while a check is unfinished or
red, or with a recorded commit that is not the pull request head, fails the
gate and comes back to you as a new attempt with the failing checks in
`gc.attempt_log` on the gate's control bead (the dependent of this step
bead); after three attempts the step fails and the re-review does not start.

Before closing this step, run `git checkout --detach` in your own directory so
the branch is not left held by your slot for the next session that needs it.
