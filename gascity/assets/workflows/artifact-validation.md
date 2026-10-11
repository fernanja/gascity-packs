## Artifact validation: schema ids and the one validator command

Every build artifact is Markdown with YAML front matter whose `schema` field
names one of the ids below. The id is never the file name: `plan-review.md`
carries `gc.build.plan_review.v1` (underscore), and `plan-review`,
`plan_review` or `gc.build.plan-review.v1` are not schemas (gc-0dbwj9,
gcas-voo4d2: a finalize step validated the plan review against `plan-review`
and reported it invalid; gcas-6ut42h). The table is derived from the schema
files under `schemas/build/` in this pack, `schemas/build/<name>.v1.yaml`
(never `.toml`, never beside the formulas; gc-gxa9q); a schema file and this
table change together.

| Artifact file (conventional name) | `schema` / `--schema` id | Written by |
| --- | --- | --- |
| `requirements.md` | `gc.build.requirements.v1` | requirements stage (or the mayor, by hand) |
| `implementation-plan.md` | `gc.build.plan.v1` | plan stage |
| `plan-review.md` | `gc.build.plan_review.v1` | plan-review stage |
| `decomposition.md` | `gc.build.decomposition.v1` | decompose stage |
| `task-<bead-id>-summary.md`, `implementation-summary.md` | `gc.build.implementation-summary.v1` | implementation item, summarize, summarize-implementation stages |
| `review.md`, `re-review*.md`, review reports | `gc.build.review.v1` | review, re-review and code-review stages |
| `final-report.md`, `factory-run.md` | `gc.build.final-report.v1` | finalize stage |

The validator has one entrypoint, its command line. It is
`assets/scripts/validate_build_artifact.py` in the pinned pack; there is no
`validate_artifact_text` command and no other script. Resolve the pinned copy
through gc's formula layers (the last layer that ships it wins, exactly as the
controller's gate resolves its check script) and run it from the launcher rig
root:

```bash
python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema <schema id from the table> --path <artifact file>
```

It prints `{"ok": true, "schema": "<id>"}` and exits 0 when the artifact is
valid; otherwise it exits 1 with one `error: ...` line naming the first
problem (a missing front-matter field, a status outside the schema, a
coverage entry without a rationale, `markdown coverage matrix is missing`, a
`missing required body section '<name>'`), and nothing else. Fix that, run it
again, repeat until it passes. The Markdown coverage matrix it looks for is a
table whose header row has an `id` column and a `status` column (case does
not matter), one row per `trace.coverage` entry with the same id and status.

A producing step closes under the gate `assets/scripts/checks/build-artifact-valid.sh`,
which reads the artifact path from the workflow root metadata keys the step
declares and runs the same validator. Run the gate yourself before closing
such a step, from the launcher rig root (`gc.work_dir` on the workflow root):

```bash
GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"
```

A consuming step (a `prepare-*` gate, finalize) validates each input it reads
with the command line above and the id from the table, and fails closed on a
non-zero exit: an approved artifact that does not validate stops the build at
that step, with the validator's `error:` line in `gc.restart.reason`.
