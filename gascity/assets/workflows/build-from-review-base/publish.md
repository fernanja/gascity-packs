This is the `build-from-review-base` publish stage.

Read push {{push}}, open_pr {{open_pr}}, and the finalized continuation outcome
from the workflow root metadata. If neither publishing action is explicitly
authorized, no-op and record `not_published`.

Publishing disabled or no-op status must never convert a blocked, failed, or
repairable finalization into a passing workflow outcome. Preserve
`gc.outcome=fail`, `gc.build.status=blocked`, `gc.failure_class`, and
`gc.restart.*` metadata when finalize recorded them.

This step is the workflow's terminal step for outcome purposes: whether the
whole workflow reads as pass or fail is derived from THIS step's own claimed
bead outcome, not just from the workflow root's metadata. Completing this
step without an internal error (a correct no-op when publishing is disabled,
or a correctly-skipped publish because finalize recorded a block) is not the
same thing as the reviewed work being approved -- do not close THIS step's
own claimed bead with `gc.outcome=pass` just because the step itself ran
cleanly. When finalize recorded a blocked, failed, or repairable state, close
THIS step's own claimed bead with the same outcome: `gc bd update
"<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then `gc bd close
"<claimed-step-id>" --reason "<concise reason, e.g. publish correctly
skipped -- workflow blocked upstream>"`. Only set `gc.outcome=pass` on this
step when finalize recorded an approved, passing continuation.

Read that state from the workflow root (`gc.build.status`, `gc.outcome`,
`gc.failure_class`), not from the finalize step's bead: finalize closes its own
bead with `gc.outcome=pass` once a valid report is written, including a
`status: blocked` report. When the root carries `gc.build.status=blocked`, do
not push and do not open or mark ready a pull request.

If publishing is authorized, publish only after the continuation finalized
successfully and the review stage approved or explicitly allowed publication.
Record push status, PR status, or a blocked publish reason on the workflow root
and publish step before closing.

**Resolving the approved commit and deciding whether to push (gc-ajt3i).**
Never decide "already pushed" from local push/PR metadata carried over from
an earlier run or continuation, and never assume the branch is already at the
right commit without checking. Follow this protocol exactly:

1. Read `gc.build.review_subject_commit` on the workflow root. This is the
   ONLY source of truth for the approved commit -- it is the sha the review
   or repair-review stage most recently recorded against an `approved`
   verdict. If it is missing, fail closed (`gc.outcome=fail`,
   `gc.failure_class=hard`, reason `missing review_subject_commit`) rather
   than guessing from `implementation_summary_path` or any other artifact.
2. Resolve the actual remote state with `git ls-remote origin <branch>` --
   not a locally cached ref, not a "was pushed" flag from a previous
   publish attempt. Compare its commit to `gc.build.review_subject_commit`.
3. If they already match and the commit's message carries no skip-CI tag
   (`git log -1 --format=%B <sha>` has none of `[skip ci]`, `[ci skip]`,
   `[no ci]`, `[skip actions]`, `[actions skip]`), no push is needed; proceed
   straight to PR handling (if `open_pr` is authorized). A tagged commit
   still needs the push in step 4: GitHub runs no CI for it.
4. Otherwise fast-forward push `gc.build.review_subject_commit` to `<branch>`
   on `origin`, **without a skip-CI tag** (reviewer-first, gc-gef2p8:
   every earlier push of this branch was a `[skip ci]` checkpoint, and this is
   the push that runs CI, once, on the commit the reviewer approved). When the
   commit's own message carries a skip-CI tag, put an empty commit on top of
   it first, in the scratch worktree below:
   `git commit --allow-empty -m "chore: run CI"`, and push that. The tree of
   the pushed tip must equal the approved commit's tree
   (`git diff --quiet <approved> <tip>`). If the push cannot be fast-forwarded
   (rejected, diverged, or the local worktree doesn't have that commit), fail
   loudly: do NOT open a PR, do NOT report `not_published`/`noop`, record
   `gc.outcome=fail`, `gc.failure_class=hard`, and a reason naming both the
   approved sha and the actual remote sha. A stale-head PR pointing at the
   wrong commit is worse than a blocked publish step.
5. Only after the remote branch tip is confirmed to be
   `gc.build.review_subject_commit` or that commit plus the one empty
   `chore: run CI` commit (whether by matching already or by a push that just
   succeeded) may a PR be opened. Record the sha that is now actually at the
   branch tip as `gc.build.publish_pushed_commit` on the workflow root before
   closing -- this is the sha a PR title/body may truthfully claim as
   published and the sha CI runs on.

Where to push from (gc-mpaqx): never the implementation worktree. It belongs
to a closed bead and the engine may already have removed it; the approved
commit is still in the repository. When step 4 needs a push, make your own
detached worktree of `gc.build.review_subject_commit` at
`$GC_CITY/.gc/worktrees/$GC_RIG-scratch/publish-<claimed-step-id>`, as
"Where to work" in `review.md` beside this file describes, push from there so
the pre-push hooks test that commit, and remove it before closing this step.

**This step runs CI, once (reviewer-first, gc-gef2p8).** No pull request
exists yet on a first publish: the implementation and fix passes made only
`[skip ci]` checkpoint pushes and opened none. Only after steps 1-5 above have
confirmed the remote branch tip, look for one:
`gh pr list --head <branch> --state open --json number,isDraft,headRefOid,url`.

- No open pull request: open one as a **draft** against the default branch,
  with the source bead id in the title (the bead this build was dispatched
  for) and the sanitized body from the final report:
  `gh pr create --draft --base <default-branch> --head <branch> --title "<what changed> (<source-bead-id>)" --body-file <file>`.
  Never open it ready: a merge sweep merges any clean non-draft pull request,
  and it must not be merged before its checks are green.
- One open pull request (a second publish, after a red check): do not open a
  second. Confirm its `headRefOid` equals the tip you just pushed, bring its
  title (it must contain the source bead id) and body up to date, and leave it
  a draft.
- More than one: fail closed and name them; do not guess which one carries
  the approved commit.
- If `open_pr` is not authorized, or finalize recorded a blocked, failed or
  repairable outcome, open nothing and leave an existing draft exactly as it
  is: do not mark it ready and do not close it. A draft is what keeps
  unapproved work from being merged.

Then wait for CI on the head sha, with a bound, and judge it with the pinned
gate script in manual mode, which reads check runs and commit statuses in both
shapes GitHub returns (never parse `statusCheckRollup` by hand):

1. Wait: `perl -e '$t=shift; $p=fork; exec @ARGV unless $p; $SIG{ALRM}=sub{kill "TERM",$p; exit 124}; alarm $t; waitpid $p,0; exit($? & 127 ? 128+($? & 127) : $?>>8)' 1500 gh pr checks <number> --watch --interval 30`.
   This `perl` wrapper is the time limit that works on macOS and Linux alike
   (GNU `timeout` is not installed on macOS, and a bare `alarm` before `exec`
   does not stop `gh`). It returns by itself when the checks finish (0: green,
   1: a check failed) and ends after 25 minutes (status 124), so it is not a
   blocking command of the kind the shell rules forbid. Status 128 or more
   means `gh` was ended by a signal: run it again. "No checks reported" means
   CI has not registered: wait a minute and run it again. A check that is
   running after the wrapper ends gets another bounded wait, three waits at
   most; a check that sat `queued` or `waiting` through a whole wait is stuck:
   fail this step as a hard failure naming it, do not wait again.
2. Judge: `"$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/pr-ci-green.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --repo <owner>/<repo> --pr <number> --commit <gc.build.publish_pushed_commit>`.
   Run from the launcher rig root. `PASS` means every required check on that
   head is complete and green (a failure the base branch also has passes with a
   `WARNING`; copy each `WARNING` line, with its URLs, into this step's close
   reason). Anything else, with exit 1, is a red or unfinished check.
3. On `PASS`: mark the pull request ready, `gh pr ready <number>`, record the
   PR URL, the pushed sha and the `PASS` line on the workflow root and this
   step, and close `gc.outcome=pass`.
4. On a red required check, never retry CI blind (no `gh run rerun` of a
   failed required check unless the log shows CI's own machinery died, and
   then once, recording the run URL). Leave the PR a draft. Record the failing
   check names with their URLs on the workflow root:
   `gc bd update "<workflow-root-id>" --set-metadata "gc.build.publish_ci_failures=<name>: <url>; <name>: <url>" --set-metadata "gc.build.publish_status=ci_red"`.
   Then hand the build back to the fix loop for one more LOCAL round: write a
   findings report for the red checks (schema `gc.build.review.v1`, verdict
   `changes_required`, one finding per failing check with the job, the failing
   step and the decisive log lines from `gh run view <run-id> --log-failed`),
   record it as `gc.build.review_report_path`, and launch the fix loop exactly
   as `repair-review.md` step 2 shows, with this report as `findings_path`
   and `max_iterations` reduced by the attempts already made. Record the new
   loop root as `gc.build.fix_loop_root_id`, block THIS step on it
   (`gc bd dep add "<claimed-step-id>" "<loop-root-id>"`, confirmed with
   `gc bd dep list`), and release your claim with `gc bd release-if-current`
   instead of closing, as `decide-repair.md` does for a replacement loop. The
   engine readies this step again when the loop root closes. When it is
   readied again, read the loop's final re-review: if it approved, record the
   commit it reviewed as `gc.build.review_subject_commit` and
   `gc.build.review_verdict=approved` as `decide-repair.md` prescribes, then
   run this protocol again from step 1 (push without the skip tag, wait,
   judge). If it did not approve or `max_iterations` is spent, fail: record
   `gc.outcome=fail`, `gc.failure_class=hard`, `gc.build.publish_status=failed`
   and `gc.build.publish_reason=ci_red_unresolved`, leave the PR a draft.
   Publishing is the only step that runs CI, so a second red result after the
   local round is a result to report, not a reason to loop again blind.

**Report what the pre-push checks actually said (gc-4kgy1).** When a push runs
the repo's pre-push hooks, keep their output and quote the decisive lines in
this step's close reason: each hook's pass/fail line and, for a test run, the
test count and the final `OK` or `FAILED` line. Do not write that the checks
passed without that quote. If a hook fails, the push did not happen: fix the
cause or fail this step with the hook's output; never skip or bypass a hook to
get the push through. A failure in a test the branch itself adds or changes is
not a flake and is not resolved by resetting databases or retrying.

This is exactly the gap that shipped fernanja/ascent_app#2462 on continuation
root gcas-p0g3rr: the publish step reported "branch was already pushed at
approved commit 00c89571d" -- the previous blocked run's pre-repair head --
while the real approved commit (88bc1992f, three repair commits later) was
never pushed. The remote was never actually checked against the latest
approving review's own subject commit.
