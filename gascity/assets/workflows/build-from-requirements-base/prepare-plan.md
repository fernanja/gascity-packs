This is the `build-from-requirements-base` handoff into the inherited plan suffix.

Validate that the requirements artifact now exists and is approved or otherwise
valid for planning. Do not write the implementation plan from this step.

Close only after the inherited `build-from-plan-base` suffix can consume the
requirements artifact without rerunning requirements.

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
  --set-metadata "gc.build.blocked_step=prepare-plan" \
  --set-metadata "gc.failure_class=<class from the table>" \
  --set-metadata "gc.restart.entrypoint=<entrypoint from the table>" \
  --set-metadata "gc.restart.reason=<one machine-readable clause>"
```

| What failed | `gc.failure_class` | `gc.restart.entrypoint` |
| --- | --- | --- |
| The requirements artifact is missing or its status is not approved | `requirements_not_approved` | `build-from-requirements` |

Then close this step as failed:
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then
`gc bd close "<claimed-step-id>" --reason "<what failed and where to restart>"`.
The engine skips every later build step and runs `finalize` once. Finalize
writes the blocked report from what this step recorded, so the values above
are what the next person restarts from.
