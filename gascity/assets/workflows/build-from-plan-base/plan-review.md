This is the `build-from-plan-base` plan-review stage.

Review the implementation plan before decomposition. The verdict must map to
approved, questions, changes_required, or blocked, and it must honor
interaction_mode {{interaction_mode}}.

Treat a deferred requirement as a blocking finding. Go through every
requirement and acceptance criterion in the requirements artifact and find
where the plan delivers it. If the plan marks one as deferred, out of scope,
report-only, "later", or to be done by another stage, the verdict is
`changes_required` unless the requirements artifact itself permits that
deferral in so many words. Name the criterion and quote the plan sentence. A
criterion the plan never mentions is the same finding (gc-gdyaz: a plan
postponed a required check, the plan review approved it, and the code review
found the gap three hours later).

Check scope reachability: confirm every acceptance criterion can be met along
the plan's traced call path without breaking the requirements' scope
constraints (e.g. "no edits under X"). If a scope ban makes a criterion
unreachable, record a blocking finding naming the criterion, the ban, and the
call-path hop that needs the edit. Never approve around the conflict.

Treat an acceptance criterion with no observation method, or one the test
environment cannot observe with no stated alternative verification, as a
blocking finding.

A check, test or probe in the plan that cannot fail when the mechanism it
verifies is wrong is `must-address`, never `advisory`. It cannot fail when it
exercises only the path the mechanism itself implements (a checksum loop that
prints ok for a swapped archive; a crash-sweep probe that only runs when no
crash happened). Name the counter-case to add in the note: the input or state
for which the mechanism is wrong and the check must fail. Plan review rated
these advisory twice and both reached review as defects (native-verification
checksum loop, gcas-8kiek63 implementation-plan.md:247-251; test-slot crash
sweep probe, gcas-fip716u :270-273 and :401-404).

Re-run the plan's consumer searches. The plan lists, for every changed
interface, test id, selector, helper, CLI flag, file path or config key, all of
its consumers with the exact search command and its hit count. Re-run at least
one of those searches yourself, on the current base branch, and compare the hit
count. A count that differs, or a changed item that has no listed search, is a
`must-address` note that names the search and both counts.

Mark every specific, actionable note (e.g. "measure first", "X is the page's
responsibility") as `advisory` or `must-address`, and list the must-address
notes under `## Must-Address Notes` in the plan-review artifact.
Decomposition copies each one into its owning work item as a checklist item.

Write the plan-review artifact to `{{plan_review_path}}` when supplied;
otherwise write it under `{{artifact_root}}`. Its YAML front matter opens with
`schema: gc.build.plan_review.v1` and carries, besides the `workflow`, `reviewer`,
`plan`, `requirements` and `base` blocks you already write: `producer` (`formula:
build-from-plan`, `stage: plan-review`, `attempt: <n>`), `verdict`, `status` equal
to the verdict (`approved`, `questions`, `changes_required`, or `blocked`), and
`trace` with `upstream` (the plan and the requirements, each as `path` and
`hash: sha256:<sha256>`) and `coverage` (one `{id, status}` per requirement id
the requirements artifact defines: `covered` when the plan covers it, otherwise
`blocked` or `deferred` with a `rationale`). The body has the sections `## Verdict`,
`## Requirement traceability`, `## Coverage`, `## Findings`, `## Must-Address
Notes`, and `## Fix Handoff`, in that order. `## Coverage` is the Markdown
coverage matrix the validator pairs with `trace.coverage`: a table whose header
row has an `id` and a `status` column, then one row per `trace.coverage` entry
with the same id and the same status string, none missing and none added:

```markdown
## Coverage

| id | status | where the plan delivers it |
| --- | --- | --- |
| REQ-1 | covered | §3 step 2 |
| AC-2 | deferred | permitted by requirements §Non-goals |
```

A requirement-by-requirement table under another heading, or without an `id`
and a `status` column, does not count: the validator reports `markdown
coverage matrix is missing` (gc-th9ra, gcas-ji08fk: approved plan reviews with
a full `trace.coverage` and a `| id | where the plan delivers it | verified |`
table failed at the review handoff and at finalize, a step and a day after
they were approved). Record the artifact path on the workflow root (next
paragraph), then run the gate yourself before closing, from the launcher rig
root, with the same script the controller runs when this step closes:
`GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`.
Fix every reported error in the artifact until it prints `build artifact
valid`: the validator's failures belong in this step's log, never in a
consumer's (gc-d09g6; gc-bzgp69: plan reviews declared this schema for weeks
while no schema file existed, so nothing validated them). Before closing this step, resolve
the workflow root bead id from `gc.root_bead_id` on this step bead, then update
THAT root bead's metadata (not this step bead's own metadata) with
`gc.var.plan_review_path=<the resolved plan-review artifact path>`. The
inherited `build-from-decompose-base` suffix reads `plan_review_path` as a
required var from workflow root metadata — recording it only on this step bead
leaves that var empty and fails the handoff. Close only after an approved or
equivalent pass verdict is recorded, or after a blocked/changes-required verdict
is recorded with a concrete reason.

## Closing this step

Always set `gc.outcome` on this step's own claimed bead before closing it.
This step is a member of the build's planning scope: closing it with
`gc.outcome=fail` stops the build.

A review that reached a verdict closes with `gc.outcome=pass`, whatever the
verdict: `gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`,
then `gc bd close "<claimed-step-id>" --reason "<verdict and one-line reason>"`.
The verdict lives in the plan-review artifact. The next step,
`prepare-decompose`, reads it and stops the build when it is not approved.

Artifact validation: this stage is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.plan_review_path` (fallback `gc.var.plan_review_path`) on the workflow root against schema `gc.build.plan_review.v1`, whatever the verdict. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact. The schema id of this artifact is `gc.build.plan_review.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.plan_review.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).

Close with `gc.outcome=fail` only when no verdict could be produced, for
example because the plan artifact is missing or unreadable. Record the stop on
the workflow root first: `gc.build.status=blocked`,
`gc.build.blocked_step=plan-review`, `gc.failure_class=plan_review_failed`,
`gc.restart.entrypoint=build-from-plan`, and `gc.restart.reason` with one
machine-readable clause.
