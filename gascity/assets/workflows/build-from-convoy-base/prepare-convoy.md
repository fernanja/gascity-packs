This is the `build-from-convoy-base` convoy handoff step.

Validate the implementation convoy before implementation drains run. Accept the
convoy from `{{implementation_convoy_id}}` when supplied; otherwise read the
convoy ID recorded on the workflow root as `gc.input_convoy_id`.

The convoy must contain only runnable implementation work items for this build
continuation. Reject planning, review, workflow-control, or original request
convoys. Record the resolved convoy as both:

- `gc.input_convoy_id=<implementation-convoy-id>`
- `gc.build.implementation_convoy_id=<implementation-convoy-id>`

Close only after the convoy identity, drain policy {{drain_policy}}, selected
implementation target {{implementation_target}}, and source artifact paths are
validated and recorded.

## Closing this gate

This step is a member of the build scope with `gc.on_fail=abort_scope`. Its own
`gc.outcome` decides whether the build continues, so always set it before
closing. A bead closed with no `gc.outcome`, or with any value other than
`pass`, counts as a failure and stops the build.

**Validation passed.** Run
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then
`gc bd close "<claimed-step-id>" --reason "<what was validated>"`.

**Validation failed.** Do not pass, and do not start or repair the work this
gate guards. Record the stop on the workflow root first. The root id is
`gc.root_bead_id` on this step bead:

```bash
gc bd update "<workflow-root-id>" \
  --set-metadata "gc.build.status=blocked" \
  --set-metadata "gc.build.blocked_step=prepare-convoy" \
  --set-metadata "gc.failure_class=<class from the table>" \
  --set-metadata "gc.restart.entrypoint=<entrypoint from the table>" \
  --set-metadata "gc.restart.reason=<one machine-readable clause>"
```

| What failed | `gc.failure_class` | `gc.restart.entrypoint` |
| --- | --- | --- |
| No implementation convoy is recorded, or it does not exist | `implementation_convoy_missing` | `build-from-decompose` |
| The convoy holds planning, review, workflow-control, or original-request beads, or no runnable work item | `implementation_convoy_invalid` | `build-from-decompose` |

Then close this step as failed:
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then
`gc bd close "<claimed-step-id>" --reason "<what failed and where to restart>"`.
The engine skips every later build step and runs `finalize` once. Finalize
writes the blocked report from what this step recorded, so the values above
are what the next person restarts from.
