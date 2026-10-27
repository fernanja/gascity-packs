Write the canonical build implementation summary.

This build-base stage may be inherited by concrete build methodology packs.
Concrete build methodology packs may override this stage, but any override must
preserve the root implementation-summary artifact contract.

The implementation drain may write one or more per-item summaries from source
worktrees. This stage converts that implementation evidence into the root
workflow artifact recorded as `gc.build.implementation_summary_path`, normally
`implementation-summary.md` under the build artifact root.

Resolve the workflow root bead and artifact root from root metadata. If
`gc.build.implementation_summary_path` is empty, derive an absolute path under
`gc.var.artifact_root` or `gc.build.artifact_root` as
`implementation-summary.md`, then record it on the workflow root:

`gc bd update "<workflow-root-id>" --set-metadata "gc.build.implementation_summary_path=<absolute path>"`

Do not use `gc bd update --metadata 'key=value'`; `--metadata` only accepts a JSON
object.

Collect the closed implementation source anchors and drain child workflows from
the implementation convoy. Read their recorded implementation summary paths
from `gc.implementation.summary_path`, `gc.build.implementation_summary_path`,
or `gc.var.summary_path`. Include those summaries as upstream evidence. If an
item summary is missing coverage IDs that appear in the requirements artifact,
read the requirements, decomposition, review context, and verification evidence
before writing the canonical root summary. The canonical summary must cover all
accepted requirement IDs that the build finalized.

Bridge the hand-off from the items to the build root (reviewer-first,
gc-gef2p8). Each implementation item records the commit it hands over as
`gc.build.handoff_commit` and `gc.build.handoff_branch` on its own item root;
the review reads them from the build root, so this step is the writer. Read
both keys from each implementation item's root, in the drain order (the order
the items ran, which the implementation convoy lists). Record the values of the
LAST item (the last item's head contains every earlier item's commits) on the
workflow root under the same keys, plus `gc.build.handoff_items`, the ordered
item root ids, comma-separated:

`gc bd update "<workflow-root-id>" --set-metadata "gc.build.handoff_commit=<last item's commit>" --set-metadata "gc.build.handoff_branch=<last item's branch>" --set-metadata "gc.build.handoff_items=<item-root-1>,<item-root-2>"`

Before you record the bridge above, check every item root: when it lacks one
or both keys although the workflow publishes
(`gc.var.push` and `gc.var.open_pr` both `true` on the workflow root), derive
them; do not bridge nothing (gc-jhuzd0: two of the first four items closed with
a correct checkpoint on origin and no keys). Read that item's close reason and
its implementation summary for the branch and the commit sha, then verify them
on the shared repository before you record anything: the sha exists
(`git cat-file -e <sha>^{commit}`), is an ancestor of the branch head
(`git merge-base --is-ancestor <sha> origin/<branch>`, after `git fetch origin
<branch>`), carries a `[skip ci]` tag, and the branch is not the default
branch. Record the full 40-character sha (`git rev-parse <sha>^{commit}`),
never an abbreviated sha copied from the text. When the text names the branch
but no sha, use the branch head on origin (`git rev-parse origin/<branch>`)
only when that commit carries the `[skip ci]` tag; otherwise the sha is
underivable. Record the derived pair on the item root AND on the workflow root,
and say in the summary's Verification section which items you derived, from
which text, and which checks passed:

`gc bd update "<item-root-id>" --set-metadata "gc.build.handoff_commit=<sha>" --set-metadata "gc.build.handoff_branch=<branch>"`

Fail only when nothing can be derived (no sha and no branch in the close reason
or the summary, a branch whose origin head carries no `[skip ci]` tag, or a
derived pair that fails a check): close this step as a hard
failure with `gc.failure_class=hard` and a `gc.failure_reason` that names the
item root and what was missing.

When no item recorded a hand-off and the workflow does not publish, write
nothing and say so in the summary's Verification section. When the items name
different branches, do not guess: say so in Remaining Risks and record the last
item's pair anyway, because the review will check the commit it reads out.

Write the artifact as Markdown with YAML front matter, not JSON. Use mapping objects for front matter; do not use scalar shortcuts such as
`workflow: build-basic`. The top-level YAML shape must be:

- `schema: gc.build.implementation-summary.v1`
- `workflow: {id: <workflow-root-id>, formula: <root-workflow-formula>}`
  `workflow.id` is THIS workflow's root bead id: the `gc.root_bead_id` value on
  the claimed step bead, read now, never a value copied from another work
  item's summary file. The gate passes that id to the validator and rejects
  any other (gc-d09g6: task-gcas-onsl2r-summary.md named the previous item's
  root gcas-o8gynk, and only the consumer noticed).
- `methodology: {pack: <pack-name>, name: <build-formula>}`
- `producer: {formula: <build-formula>, stage: summarize-implementation, attempt: <positive integer>}`
- `status: approved` or another schema-allowed status
- `trace: {upstream: [...], coverage: [...]}`

Trace front matter must use the validator shape exactly:

- `trace.upstream[]` entries must include `path` and `hash`; do not use
  `id`/`title`/`type` entries as the upstream shape.
- For upstream build artifacts or implementation summaries, use their recorded
  paths and scheme-qualified hashes such as `sha256:<digest>`. For convoy or
  bead inputs, use `path: beads/<bead-id>` and `hash: bead:<bead-id>`.
- If an upstream entry lists `ids`, every listed id must appear exactly once in
  `trace.coverage` and in the Markdown coverage table with the same status.
- Coverage statuses are not artifact statuses. Use `covered` for satisfied
  requirements; do not use `approved` in `trace.coverage[].status` or the
  Markdown coverage table.

Include a Markdown coverage table whose ID/status pairs exactly match
`trace.coverage`. The validator only recognizes a Markdown table with an `ID`
column and a `Status` column. Use this shape:

| ID | Status |
| --- | --- |
| REQ-001 | covered |

The body must include these schema-required sections:

- Summary
- Intended Behavior
- Changed Files
- Verification
- Remaining Risks

In those sections, include the implementation convoy id, source anchor ids,
per-item summary paths, changed files, first verification commands, final proof
commands, observed pass/fail results, and remaining risks. Keep the root
summary concise, but do not omit accepted requirement IDs.

Before closing this step, read the launcher rig root from the workflow root bead's `gc.work_dir`, then run the pinned pack's copy of the same validator locally from that launcher rig root (the command resolves the gate script through gc's formula layers, exactly as the controller does):

When the root carries no `gc.work_dir`, the launcher rig root is the rig named by `gc.var.rig_name` on the root (`gc rig list --json` path), which is your session's `git rev-parse --show-toplevel`; use that and do not derive it from `--git-common-dir`.

`GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`

fix every reported validation error before setting `gc.outcome=pass`. Then set
the claimed step outcome with
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, and close
with `gc bd close "<claimed-step-id>" --reason "<concise reason>"`. Do not pass
`--metadata` or `--set-metadata` to `gc bd close`.

Artifact validation: this stage is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.implementation_summary_path` against schema `gc.build.implementation-summary.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the summary in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact. The schema id of this artifact is `gc.build.implementation-summary.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.implementation-summary.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).
