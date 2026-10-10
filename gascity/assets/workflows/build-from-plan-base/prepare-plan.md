This is the `build-from-plan-base` planning handoff step.

Validate that the approved requirements artifact exists at
`{{requirements_path}}` and that artifact_root {{artifact_root}},
context_path {{context_path}}, plan_path {{plan_path}}, and
plan_review_path {{plan_review_path}} are safe plain paths. Record the selected
planning_formula {{planning_formula}} for downstream traceability.

Do not run decomposition or implementation from this step. Record
`gc.build.continuation_entrypoint=plan` when this suffix is launched directly.
Close only after the plan stage can write or reuse the selected plan artifact.

**Input gate.** "Approved" is the file's own `status` field; this step also
requires the file to validate against its schema, and the controller checks
that itself when this step closes (the artifact gate below). Run the
validator on `{{requirements_path}}` with schema `gc.build.requirements.v1`
before anything else (the exact command is under "Validating inputs"). A
non-zero exit is a stop, not a warning: a requirements file missing a
required section (`missing required body section 'W6H'`,
`'Problem Statement'`) stops the build here, at minute one, instead of being
found by `prepare-review` or finalize after the implementation ran
(gcas-6ut42h: three mayor-written requirements files were missing `W6H` or
`Problem Statement`; the builds proceeded and the gap surfaced a day later).
Do not edit the requirements file to make it pass: an approved artifact is
the launcher's, and a corrected file needs a new `requirements_path`. Record
the validator's `error:` line verbatim in `gc.restart.reason`.

Validating inputs: every artifact this step reads is validated with the
pinned validator's command line, never by eye and never with a guessed
entrypoint or schema id (gc-0dbwj9). The id is the `schema` field below, not
the file name, and the full table with the command is
`assets/workflows/artifact-validation.md` in the pinned pack:

| Artifact file | `--schema` id |
| --- | --- |
| `requirements.md` | `gc.build.requirements.v1` |
| `implementation-plan.md` | `gc.build.plan.v1` |
| `plan-review.md` | `gc.build.plan_review.v1` |
| `decomposition.md` | `gc.build.decomposition.v1` |
| `task-<bead-id>-summary.md`, `implementation-summary.md` | `gc.build.implementation-summary.v1` |
| `review.md`, re-review reports | `gc.build.review.v1` |
| `final-report.md`, `factory-run.md` | `gc.build.final-report.v1` |

```bash
python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema <id from the table> --path <artifact file>
```

Exit 0 (`{"ok": true}`) is valid; exit 1 prints one `error:` line naming the
first problem, which goes into the stop record verbatim.

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
  --set-metadata "gc.build.blocked_step=prepare-plan" \
  --set-metadata "gc.failure_class=<class from the table>" \
  --set-metadata "gc.restart.entrypoint=<entrypoint from the table>" \
  --set-metadata "gc.restart.reason=<one machine-readable clause>"
```

| What failed | `gc.failure_class` | `gc.restart.entrypoint` |
| --- | --- | --- |
| The requirements artifact at `{{requirements_path}}` is missing or its status is not approved | `requirements_not_approved` | `build-from-plan` |
| The requirements artifact does not validate against `gc.build.requirements.v1` (the validator names the first missing field or section) | `requirements_artifact_invalid` | `build-from-plan` |
| A path input is unsafe or unusable | `plan_inputs_invalid` | `build-from-plan` |

Restart at `build-from-plan` with a corrected `requirements_path` or corrected
paths: this entrypoint consumes requirements, it does not write them.

Then close this step as failed:
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then
`gc bd close "<claimed-step-id>" --reason "<what failed and where to restart>"`.
The engine skips the planning steps that have not run, and the implementation
drain closes without dispatching any work. `prepare-review` then ends the
build without a review, and `finalize` runs once. Finalize writes the blocked
report from what this step recorded, so the values above are what the next
person restarts from.

Artifact validation: this gate is checked by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.var.requirements_path` (fallback `gc.build.requirements_path`) on the workflow root against schema `gc.build.requirements.v1` when this step closes. It is an input gate with one attempt: a failed check closes this step `gc.outcome=fail` and stops the planning scope, exactly as a validation failure you record yourself does, so run the validator first and record the stop (table above) rather than letting the check find it. The check does not repair anything and there is no second attempt; the launcher restarts `build-from-plan` with a corrected `requirements_path`. The schema id of this artifact is `gc.build.requirements.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.requirements.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).
