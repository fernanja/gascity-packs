Use the built-in Gas City shared-session implementation drain.

Drain the approved decomposition in one shared worktree with the inherited
artifact root, context path, implementation target, iteration limit, push flag,
and open PR flag. Store the implementation summary path and outcome on the
workflow root bead.

Close this step only after implementation reports a clean result or an explicit
failure artifact.

This step coordinates the drain; it does not itself write source. If coordinating
the drain ever tempts a commit onto the launcher checkout to unblock validation,
that is never valid — the drained `do-work-item` items enforce their own
worktree isolation, and the only path from a worktree to `main` is a GitHub PR
through the pack's normal publish step.
