Scope latch for the fix loop. Not worker work: never claim, update, or close
this bead.

`plan-fixes`, `apply-fixes`, and `re-review` are members of this scope. Each
stops the loop when it closes with an explicit `gc.outcome=fail`; none carries
`gc.on_fail=abort_scope`, because `apply-fixes` and `re-review` are checked
steps and a bare close of a re-spawned attempt must not stop a healthy loop.
A step `needs` is satisfied by its predecessor closing, whatever the outcome,
so this scope is the only thing that keeps `re-review` from starting after
`apply-fixes` failed (gc-80xjc5).

When a member stops the loop, the engine closes the members that have not run
with `gc.outcome=skipped` and closes this latch with `gc.outcome=fail`, and the
loop root closes `gc.outcome=fail`. The root does not copy `gc.failure_class` or
`gc.failure_reason`: they stay on the step that failed. When every member
passes, the engine closes this latch with `gc.outcome=pass`.

Concrete methodology packs may override a member step. An override replaces the
whole step, metadata included, so it must keep the step's `gc.scope_ref` and
`gc.scope_role`: a member that loses them runs outside this scope and cannot
stop the loop.
