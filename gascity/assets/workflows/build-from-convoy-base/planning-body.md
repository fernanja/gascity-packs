Scope latch for the planning half of the build. Not worker work: never claim,
update, or close this bead.

Every build step before the implementation drain is a member of this scope.
The `prepare-*` gates carry `gc.on_fail=abort_scope`, so a gate stops the
build when it closes with `gc.outcome=fail` or with no `gc.outcome`. The other
members stop it only with an explicit `gc.outcome=fail`.

When a member stops the build, the engine closes the members that have not run
with `gc.outcome=skipped` and closes this latch with `gc.outcome=fail`. The
implementation drain waits on this latch and is not a member. When the latch
closed failed, the drain closes itself without dispatching any work. When
every member passes, the engine closes the latch with `gc.outcome=pass` and
the drain runs.
