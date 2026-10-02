Scope latch for the review half of the build. Not worker work: never claim,
update, or close this bead.

`prepare-review`, `review`, and `repair-review` are members of this scope.
`prepare-review` is the gate: it carries `gc.on_fail=abort_scope`, so it stops
the build when it closes with `gc.outcome=fail` or with no `gc.outcome`.
`review` and `repair-review` stop it only with an explicit `gc.outcome=fail`.

When a member stops the build, the engine closes the members that have not run
with `gc.outcome=skipped` and closes this latch with `gc.outcome=fail`. When
every member passes, the engine closes it with `gc.outcome=pass`. `finalize`
waits on this latch, so it runs exactly once in both cases.
