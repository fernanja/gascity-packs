This is the `build-base` finalize stage. Treat it as a virtual contract that concrete formulas may override.

Synthesize the workflow result from the requirements, plan, decomposition, implementation, and review artifacts. Record pass/fail outcome, artifact paths, and any remaining follow-up work.

Write the final build report to the resolved final report path and ensure that
path is recorded on the workflow root bead as `gc.build.final_report_path`.
Use `gc bd update "<workflow-root-id>" --set-metadata "gc.build.final_report_path=<absolute path>"`.
Do not use `gc bd update --metadata 'key=value'`; `--metadata` only accepts a JSON
object.
Before closing this step, set the claimed step outcome with
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then close
with `gc bd close "<claimed-step-id>" --reason "<concise reason>"`. Do not pass
`--metadata` or `--set-metadata` to `gc bd close`.

## Final report shape

The final report is a `gc.build.final-report.v1` artifact: Markdown with YAML
front matter, mapping objects (never `workflow: build-from-plan`), and the
coverage in BOTH forms the validator reads, which must match entry for entry:

```markdown
---
schema: gc.build.final-report.v1
workflow: {id: <workflow-root-id>, formula: <root workflow formula>}
methodology: {pack: gascity, name: <root workflow formula>}
producer: {formula: <root workflow formula>, stage: finalize, attempt: <positive integer>}
status: approved   # or blocked
trace:
  upstream:
    - {path: <requirements path>, hash: sha256:<digest>, ids: [REQ-1, AC-2]}
    - {path: <review report path>, hash: sha256:<digest>}
  coverage:
    - {id: REQ-1, status: covered}
    - {id: AC-2, status: blocked, rationale: <why the build did not deliver it>}
---

## Summary

## Outcome

## Coverage

| id | status |
| --- | --- |
| REQ-1 | covered |
| AC-2 | blocked |

## Artifacts

## Remaining Risks
```

The YAML `trace.coverage` list and the Markdown table are the same data twice
and MUST MATCH: the same ids, the same status strings, none missing and none
added (the validator compares them and reports `markdown coverage matrix
must match YAML coverage`). The table's header row must have an `id` column
and a `status` column, in that spelling (case does not matter, `| ID | Status |`
is the same header); a table headed `Requirement` or `Result` is not found at
all and the report fails with `markdown coverage matrix is missing`
(gc-u7am1s: two finalize retries on 2026-10-09/10; gc-d09g6: gcas-tdkr6s).
Do not add a second table that has both an `id` and a `status` column unless
it repeats exactly the same pairs. Coverage statuses are not artifact
statuses: `covered`, `not_applicable`, `deferred`, `blocked`, `out_of_scope`,
`superseded`; never `approved`. The required `##` sections are `Summary`,
`Outcome`, `Artifacts`, `Remaining Risks`, in that order.

Anything the report says about CI comes from the pinned gate, not from your
own reading of GitHub: cite `gc.build.ci_gate_result` on the workflow root
(written by `../assets/scripts/checks/pr-ci-green.sh`, which reads check runs
and commit statuses in both shapes GitHub returns); if the key is absent, run
that script once and cite its result. In the reviewer-first flow (gc-gef2p8)
finalize runs before CI: the result is `reviewer-first: CI deferred to
publish`, the report says CI has not run yet and does not claim green checks,
and the publish step records the real result. Never parse `statusCheckRollup` or the
checks API by hand here: its entries mix `CheckRun` (a `conclusion`) and
`StatusContext` (a `state`), and a hand-rolled check rejected the second shape
and cost a retry (gc-rjjb1o: gcas-e8nvpe, PR #2744).

Before closing this step, record the report path on the workflow root as
`gc.build.final_report_path` (`gc bd update "<workflow-root-id>"
--set-metadata "gc.build.final_report_path=<absolute path>"`), then run the
gate yourself from the launcher rig root (`gc.work_dir` on the workflow
root), with the same script the controller runs when this step closes:
`GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`.
Fix every reported error in the report until it prints `build artifact
valid`: the validator's failures belong in this step's log, never in a later
step's or a human's (gc-d09g6).

When the root carries no `gc.work_dir`, the launcher rig root is the rig named by `gc.var.rig_name` on the root (`gc rig list --json` path), which is your session's `git rev-parse --show-toplevel`; use that and do not derive it from `--git-common-dir`.

Close this step only after the workflow root bead has the final outcome metadata needed by publish.

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
