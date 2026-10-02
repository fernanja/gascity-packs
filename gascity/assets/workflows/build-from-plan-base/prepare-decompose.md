This is the `build-from-plan-base` handoff into the inherited decompose suffix.

Validate that the plan and plan-review artifacts now exist, that the
plan-review verdict approves decomposition, and that downstream decompose
selectors are present.

Do not create implementation beads in this handoff step. Close only after the
inherited `build-from-decompose-base` suffix has the approved plan inputs it
requires.

## Closing this gate

This step is a gate in the build's planning scope and carries
`gc.on_fail=abort_scope`. Its own `gc.outcome` decides whether the build
continues, so always set it before closing. A bead closed with no
`gc.outcome`, or with any value other than `pass`, counts as a failure and
stops the build.

**Validation passed.** Run
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then
`gc bd close "<claimed-step-id>" --reason "<what was validated>"`.

**Validation failed.** Do not pass, and do not start or repair the work this
gate guards. Record the stop on the workflow root first. The root id is
`gc.root_bead_id` on this step bead:

```bash
gc bd update "<workflow-root-id>" \
  --set-metadata "gc.build.status=blocked" \
  --set-metadata "gc.build.blocked_step=prepare-decompose" \
  --set-metadata "gc.failure_class=<class from the table>" \
  --set-metadata "gc.restart.entrypoint=<entrypoint from the table>" \
  --set-metadata "gc.restart.reason=<one machine-readable clause>"
```

| What failed | `gc.failure_class` | `gc.restart.entrypoint` |
| --- | --- | --- |
| The plan-review verdict is `changes_required`, `blocked`, or `questions` | `plan_review_not_approved` | `build-from-plan` |
| The plan or plan-review artifact is missing, or the review is of a different plan | `plan_artifacts_missing` | `build-from-plan` |
| A downstream decompose selector is missing | `decompose_inputs_invalid` | `build-from-decompose` |

A plan that was not approved has to be written again, so the restart is
`build-from-plan`, never a later entrypoint. Put the review verdict and the
plan-review artifact path in `gc.restart.reason` and the close reason.

Then close this step as failed:
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then
`gc bd close "<claimed-step-id>" --reason "<what failed and where to restart>"`.
The engine skips the planning steps that have not run, and the implementation
drain closes without dispatching any work. `prepare-review` then ends the
build without a review, and `finalize` runs once. Finalize writes the blocked
report from what this step recorded, so the values above are what the next
person restarts from.
