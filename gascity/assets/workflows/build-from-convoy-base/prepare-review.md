This is the `build-from-convoy-base` handoff into the inherited review suffix.

Read the drain result for the implementation convoy and record the
implementation evidence path as `gc.implementation.summary_path` or
`gc.build.implementation_summary_path` on the workflow root. Then validate the
inputs required by `build-from-review-base`.

Do not review or fix code in this step. Close only after the review suffix can
consume the implementation evidence without inspecting drain internals.

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

This step is a gate in the build's review scope and carries
`gc.on_fail=abort_scope`. Its own `gc.outcome` decides whether the build
continues, so always set it before closing. A bead closed with no
`gc.outcome`, or with any value other than `pass`, counts as a failure and
stops the build.

**The planning half stopped.** Check this before validating anything. The
planning half stopped when the workflow root carries `gc.build.blocked_step`
naming a step before the drain, or when the implementation drain this step depends on closed with
`gc.failure_reason=upstream_native_dependency_failed`: that drain never ran.
There is nothing to review. Do not validate, and do not record anything on
the workflow root: the step that stopped the build recorded the cause, or
`finalize` will. Close this step with
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then
`gc bd close "<claimed-step-id>" --reason "planning stopped; nothing to review"`.

**A drain that closed failed after running.** A drain closes
`gc.outcome=fail` when any work item failed. That alone is not a reason to
fail this gate. Decide from the evidence: pass when implementation evidence
exists for the work that was done, and let `review`, `repair-review` and `decide-repair` deal
with what is missing or broken.

**Validation passed.** Run
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then
`gc bd close "<claimed-step-id>" --reason "<what was validated>"`.

**Validation failed.** Do not pass, and do not start or repair the work this
gate guards. Record the stop on the workflow root first. The root id is
`gc.root_bead_id` on this step bead:

```bash
gc bd update "<workflow-root-id>" \
  --set-metadata "gc.build.status=blocked" \
  --set-metadata "gc.build.blocked_step=prepare-review" \
  --set-metadata "gc.failure_class=<class from the table>" \
  --set-metadata "gc.restart.entrypoint=<entrypoint from the table>" \
  --set-metadata "gc.restart.reason=<one machine-readable clause>"
```

| What failed | `gc.failure_class` | `gc.restart.entrypoint` |
| --- | --- | --- |
| The drain ran but produced no implementation evidence, and none can be rebuilt | `implementation_evidence_missing` | `build-from-convoy` |
| An input the review suffix requires is missing | `review_inputs_invalid` | `build-from-review` |

Fail this gate only when the evidence cannot be recovered. When the drain's
commits exist but the recorded summary file is gone (it lived in a worktree
that was reaped), rebuild the summary under `{{artifact_root}}` from the
drain item roots (commit shas, changed files, verification run), record that
path on the workflow root, and pass. A failure here stops the build before
review.

Then close this step as failed:
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then
`gc bd close "<claimed-step-id>" --reason "<what failed and where to restart>"`.
The engine skips `review`, `repair-review`, and `decide-repair` and runs
`finalize` once.
Finalize writes the blocked report from what this step recorded, so the
values above are what the next person restarts from.
