Scope latch for the build work steps. Not worker work: never claim, update, or
close this bead.

Every build step before `finalize` is a member of this scope with
`gc.on_fail=abort_scope`. When a member closes with a failed outcome, the
engine skips the members that have not run and closes this latch with
`gc.outcome=fail`. When every member passes, the engine closes it with
`gc.outcome=pass`. `finalize` waits on this latch, so it runs exactly once in
both cases.
