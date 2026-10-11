Finalize the `build-basic` workflow.

Summarize requirements, implementation-plan, design-review, create-beads,
implementation, and review artifacts. Record the final outcome, artifact paths,
and remaining follow-up beads on the workflow root bead.

The build-basic implementation result may live in a source anchor/worktree. A
launcher rig root that still contains the original fixture is not a partial build
when the canonical implementation summary and review artifact show the source
anchor/worktree passed. Use `status: approved` for the final report in that
case, and record publish/no-op details separately.

Write the final report, normally `factory-run.md`, at the path recorded on the
workflow root bead as `gc.build.final_report_path`. The artifact must be Markdown with YAML front
matter, not JSON. Its front matter must declare
`schema: gc.build.final-report.v1`, the workflow id/formula, the methodology
pack/name, the producer formula/stage/attempt, `status`, and `trace` with
upstream and coverage entries. Include a Markdown coverage table whose
ID/status pairs exactly match `trace.coverage`.
The validator only recognizes a Markdown table with an `ID` column and a
`Status` column. Use this shape:

| ID | Status |
| --- | --- |
| REQ-001 | covered |

The YAML `trace.coverage` list and this Markdown table are the same data
twice and MUST MATCH: the same ids, the same status strings, none missing
and none added. The header row must have an `id` column and a `status`
column (case does not matter; `| id | status |` is the same header); a table
headed `Requirement` or `Result` is not found at all and the report fails
with `markdown coverage matrix is missing` (gc-u7am1s, gc-d09g6).

Before writing `factory-run.md`, ensure the canonical implementation summary
exists at the path recorded on the workflow root bead as
`gc.build.implementation_summary_path`, normally `implementation-summary.md`.
If that path is missing, absent on disk, or not a valid
`gc.build.implementation-summary.v1` artifact, synthesize the canonical
`implementation-summary.md` from closed implementation source anchors and their
recorded per-item `gc.implementation.summary_path` values. The synthesized
artifact must be Markdown with YAML front matter, schema
`gc.build.implementation-summary.v1`, the same trace shape and `ID`/`Status`
coverage matrix described here, and these sections:

- Summary
- Intended Behavior
- Changed Files
- Verification
- Remaining Risks

Record the canonical path on the workflow root bead before validating or
writing the final report. Use
`gc bd update "<workflow-root-id>" --set-metadata "gc.build.implementation_summary_path=<absolute path>"`.
Do not use `gc bd update --metadata 'key=value'`; `--metadata` only accepts a JSON
object.

Use mapping objects for front matter; do not use scalar shortcuts such as
`workflow: build-basic`. The top-level YAML shape must be:

- `schema: gc.build.final-report.v1`
- `workflow: {id: <workflow-root-id>, formula: build-basic}`
- `methodology: {pack: gascity, name: build-basic}`
- `producer: {formula: build-basic, stage: finalize, attempt: <positive integer>}`
- `status: approved` or another schema-allowed status
- `trace: {upstream: [...], coverage: [...]}`

Trace front matter must use the validator shape exactly:

- `trace.upstream[]` entries must include `path` and `hash`; do not use
  `id`/`title`/`type` entries as the upstream shape.
- For upstream build artifacts, use their recorded paths and scheme-qualified
  hashes such as `sha256:<digest>` or `git:<revision>`. For convoy or bead
  inputs, use `path: beads/<bead-id>` and `hash: bead:<bead-id>`.
- If an upstream entry lists `ids`, every listed id must appear exactly once in
  `trace.coverage` and in the Markdown coverage table with the same status.
- Coverage statuses are not artifact statuses. Use `covered` for satisfied
  requirements; do not use `approved` in `trace.coverage[].status` or the
  Markdown coverage table.
- Do not create any additional Markdown table with both an `ID` column and a
  `Status` column unless it repeats the exact same coverage ID/status pairs.
  For requirement or artifact summaries, use different column names such as
  `Requirement` and `Result`, or use `covered` as the status for every covered
  requirement.

Keep it short and useful for a first-time factory user. Include the required
schema sections:

- Summary
- Outcome
- Artifacts
- Remaining Risks

In those sections, include:

- methodology: build-basic starter factory
- requirements, plan, decomposition, implementation, and review artifact paths
- implementation convoy id
- review lanes that ran
- proof commands or test summaries that were recorded
- publish outcome
- next human action

Record the final report path on the workflow root bead as both
`gc.build.final_report_path=<path>` and `gc.build.factory_run_path=<path>`.
Use `gc bd update "<workflow-root-id>" --set-metadata "gc.build.final_report_path=<absolute path>" --set-metadata "gc.build.factory_run_path=<absolute path>"`.
Do not use `gc bd update --metadata 'key=value'`; `--metadata` only accepts a JSON
object.
Before closing this step, run the gate yourself from the launcher rig root
(`gc.work_dir` on the workflow root), with the same script the controller
runs when this step closes:
`GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`.
Fix every reported error in the report until it prints `build artifact
valid` (gc-d09g6: the validator's failures belong in this step's log, never
in a later step's). Then set the claimed step outcome with
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then close
with `gc bd close "<claimed-step-id>" --reason "<concise reason>"`. Do not pass
`--metadata` or `--set-metadata` to `gc bd close`.

Do not publish from this step.

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

Artifact validation: this stage is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.final_report_path` against schema `gc.build.final-report.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact. The schema id of this artifact is `gc.build.final-report.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.final-report.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).
