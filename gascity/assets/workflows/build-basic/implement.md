Use the built-in Gas City implementation stage contract.

Implement the approved decomposition with the inherited artifact root, context
path, drain policy, implementation target, iteration limit, push flag, and open
PR flag. Store the implementation summary path and outcome on the workflow root
bead.

Close this step only after implementation reports a clean result or an explicit
failure artifact.

This step coordinates the drain; it does not itself write source. If coordinating
the drain ever tempts a commit onto the launcher checkout to unblock validation,
that is never valid — the drained `do-work` items enforce their own worktree
isolation, and the only path from a worktree to `main` is a GitHub PR through
the pack's normal publish step.
