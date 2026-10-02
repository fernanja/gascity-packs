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
3. If they already match, no push is needed; proceed straight to PR handling
   (if `open_pr` is authorized).
4. If they differ, fast-forward push `gc.build.review_subject_commit` to
   `<branch>` on `origin`. If the push cannot be fast-forwarded (rejected,
   diverged, or the local worktree doesn't have that commit), fail loudly:
   do NOT open a PR, do NOT report `not_published`/`noop`, record
   `gc.outcome=fail`, `gc.failure_class=hard`, and a reason naming both the
   approved sha and the actual remote sha. A stale-head PR pointing at the
   wrong commit is worse than a blocked publish step.
5. Only after the remote branch tip is confirmed to equal
   `gc.build.review_subject_commit` (whether by matching already or by a
   push that just succeeded) may a PR be opened. Record the sha that is now
   actually at the branch tip as `gc.build.publish_pushed_commit` on the
   workflow root before closing -- this is the sha a PR title/body may
   truthfully claim as published.

Where to push from (gc-mpaqx): never the implementation worktree. It belongs
to a closed bead and the engine may already have removed it; the approved
commit is still in the repository. When step 4 needs a push, make your own
detached worktree of `gc.build.review_subject_commit` at
`$GC_CITY/.gc/worktrees/$GC_RIG-scratch/publish-<claimed-step-id>`, as
"Where to work" in `review.md` beside this file describes, push from there so
the pre-push hooks test that commit, and remove it before closing this step.

**The pull request usually exists already, as a draft (gc-68exu).** When the
workflow publishes, the implementation stage opens the pull request as a draft
so CI runs before review. So before opening anything, and only after steps 1-5
above have confirmed the remote branch tip equals
`gc.build.review_subject_commit`, look for it:
`gh pr list --head <branch> --state open --json number,isDraft,headRefOid,url`.

- One open pull request: do not open a second. Confirm its `headRefOid` equals
  `gc.build.review_subject_commit`; if it does not, fail exactly as step 4
  describes, naming both shas. Bring its title (it must contain the source
  bead id) and body up to date, then mark it ready for review:
  `gh pr ready <number>`. Record its URL and the pushed sha the same way a
  newly opened pull request is recorded.
- No open pull request: open one, as before.
- More than one: fail closed and name them; do not guess which one carries
  the approved commit.
- If `open_pr` is not authorized, or finalize recorded a blocked, failed or
  repairable outcome, leave an existing draft exactly as it is: do not mark it
  ready and do not close it. A draft is what keeps unapproved work from being
  merged.

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
