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

Before closing this step, run `git checkout --detach` in your own directory so
the branch is not left held by your slot for the next session that needs it.
