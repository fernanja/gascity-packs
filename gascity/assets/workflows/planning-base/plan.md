This is the `planning-base` methodology contract plan step.

Concrete methodology packs override this step to produce their native
implementation plan or design artifact. Write the plan to `{{plan_path}}` when
it is provided, and keep serialization at the artifact boundary. Record the
resolved plan path on workflow root metadata as `gc.build.plan_path` before
closing.

Budget (gc-e1dl0). Plan writing is bounded: 45 minutes of wall-clock from
the claim, or the equivalent in turns. A claim in the requirements tagged
`[source]`, `[source, mayor-verified]` or `[verified]` was checked on
`origin/main` by the mayor when the requirements were written: cite it and
plan from it, do not re-derive it (one discovery turn re-verifying such
claims took 2 hours, against 23 minutes for the whole previous plan). Read a
file only to settle something the requirements leave open. When the budget
is spent, write the plan with what you have, list each unverified assumption
under a `## Unverified` heading with what would settle it, and close: a plan
with named gaps is reviewable, a plan still in discovery is not.

Artifact validation: this step is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.plan_path` (fallback `gc.var.plan_path`) against schema `gc.build.plan.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact. The schema id of this artifact is `gc.build.plan.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.plan.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).
