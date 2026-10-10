Scope latch for the review half of the build. Not worker work: never claim,
update, or close this bead.

`prepare-review`, `review`, `repair-review`, and `decide-repair` are members of
this scope.
`prepare-review` is the gate: it carries `gc.on_fail=abort_scope`, so it stops
the build when it closes with `gc.outcome=fail` or with no `gc.outcome`.
`review`, `repair-review`, and `decide-repair` stop it only with an explicit
`gc.outcome=fail`. `decide-repair` also waits on the fix loop `repair-review`
launched: a native dependency on the loop's root bead, added at launch, keeps
it blocked until that loop closes (gc-2n9i3).

When a member stops the build, the engine closes the members that have not run
with `gc.outcome=skipped` and closes this latch with `gc.outcome=fail`. When
every member passes, the engine closes it with `gc.outcome=pass`. `finalize`
waits on this latch, so it runs exactly once in both cases.
