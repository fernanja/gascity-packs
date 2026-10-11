This is the `build-from-review-base` review handoff step.

Validate that implementation evidence exists before review runs:

- artifact_root: {{artifact_root}}
- context_path: {{context_path}}
- requirements_path: {{requirements_path}}
- plan_path: {{plan_path}}
- plan_review_path: {{plan_review_path}}
- decomposition_path: {{decomposition_path}}
- implementation_summary_path: {{implementation_summary_path}}
- code_review_formula: {{code_review_formula}}
- review_fix_formula: {{review_fix_formula}}
- implementation_formula: {{implementation_formula}}
- implementation_item_formula: {{implementation_item_formula}}
- implementation_target: {{implementation_target}}
- review_mode: {{review_mode}}
- interaction_mode: {{interaction_mode}}
- max_iterations: {{max_iterations}}

If this suffix is launched directly, require explicit implementation evidence
or a root metadata pointer to it. If this step is reached from
`build-from-convoy-base`, consume the implementation summary recorded by the
implementation suffix. Do not run implementation work from this step.

Close only after the review subject, evidence paths, selected review/fix
formulas, modes, and max iteration limit are recorded on the workflow root.

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
| `repair-summary-<step-id>.md` | `gc.build.repair-summary.v1` |
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
| Implementation evidence is missing and the workflow root records an implementation convoy (`gc.build.implementation_convoy_id`) | `implementation_evidence_missing` | `build-from-convoy` |
| Implementation evidence is missing and no implementation convoy is recorded | `implementation_evidence_missing` | `build-from-review` |
| A review selector, mode, or iteration limit is missing or invalid | `review_inputs_invalid` | `build-from-review` |

Then close this step as failed:
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=fail"`, then
`gc bd close "<claimed-step-id>" --reason "<what failed and where to restart>"`.
The engine skips `review`, `repair-review`, and `decide-repair` and runs
`finalize` once.
Finalize writes the blocked report from what this step recorded, so the
values above are what the next person restarts from.
