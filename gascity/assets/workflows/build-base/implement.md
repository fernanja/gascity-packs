This is the `build-base` implement stage. Treat it as a virtual contract that concrete formulas may override.

Implement the decomposed work using the selected drain policy and implementation target {{implementation_target}}. Keep implementation work isolated from the launcher checkout, run focused tests as work progresses, and record the implementation summary path.

The launcher checkout's `main` is never a valid commit target, for any reason, including to unblock validation against code that looks unreachable from `main`. That is a signal to re-resolve the implementation worktree or fail the step, not license to commit anything onto the shared rig root's checked-out branch. The only path from a worktree to `main` is a GitHub PR through the pack's normal publish step.

Close this step only after all assigned implementation work has either passed or has an explicit failure artifact.
