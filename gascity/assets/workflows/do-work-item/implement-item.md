
Run inside an existing shared worktree lifecycle. Resolve reserved `convoy_id`,
read `gc.drain_member_id` and `gc.drain_item_index`, validate ownership and
verification policy, validate context path {{context_path}} when set, implement
the item, write an item summary, and close only the source anchor on success.

Do not infer the source anchor from dependency ids. Read the reserved convoy and
source anchor metadata directly; when `gc bd show --json` returns a one-element
list, unwrap the first element before reading metadata. `gc.work_dir` is the
launcher rig root, not the implementation location. Use the authoritative
worktree recorded on the source anchor, run `cd "$WORKTREE"`, and verify
`pwd -P` equals `$WORKTREE` before any source read, source edit, test, file
hash, `git add`, or `git commit`.

The launcher checkout's `main` is never a valid commit target, including
under pressure to unblock validation. If verification needs code that
appears unreachable from `main` (an earlier item's commit orphaned, a
sibling worktree's work not yet merged), that is a signal to re-resolve
`WORKTREE`/the source anchor or fail the step with a clear diagnostic — not
license to cherry-pick, merge, or otherwise commit anything onto the shared
rig root's checked-out branch to make validation pass locally. The only path
from a worktree to `main` is a GitHub PR through the pack's normal publish
step; nothing before that step may write to `main` directly, for any reason.

This step must not open a GitHub pull request itself, draft or otherwise —
only push the implementation branch to the remote so downstream review,
repair-review, and the publish step (or CI) can see it. Opening a PR here,
even in draft, gives it a head branch and a mergeable state before review or
repair-review has run; an unrelated automated merge sweep that merges any
clean non-draft PR does not know this workflow is still mid-review and can
squash-merge unreviewed work the moment CI goes green (confirmed incident,
gc-5gm0d: a shared-drain item worker opened fernanja/ascent_app#2459 while
build-from-plan's implement/review/repair-review/publish steps were all
still open, and it was only kept unmerged because a human manually converted
it to draft). PR creation and readiness belong exclusively to the publish
step, after review and repair-review approve.

Write or update the item summary with these schema-required body sections,
using the exact `##` headings below in this order:

- `## Summary`
- `## Intended Behavior`
- `## Changed Files`
- `## Verification`
- `## Remaining Risks`

The `## Verification` section must include both the first verification command
and the final proof command, with the observed pass/fail result.

The final proof command MUST be `make preflight` (or `make preflight-fast` when
the rig defines that target instead), run from the worktree root after all
other verification. This is the rig's full local-CI-equivalent gate; running
it here — before push, before CI — is what lets CI trust local verification
instead of re-running everything from a red start. Record the exact exit
code. Report the result honestly whether it passes or fails: this step's own
close condition depends only on the artifact-schema validator below, not on
whether preflight itself passed — do not retry or attempt code fixes here on
a preflight failure alone, and do not withhold `gc.outcome=pass` because of
one. A failing preflight is downstream review's finding to raise and repair-
review's loop to fix, not a second retry mechanism nested inside this step.

Before closing, run this self-check and record it under `### Self-Check`
inside `## Verification`. It fails closed: if any item does not hold, fix it
and re-check before closing. It must never be left for review to find.

- Every check/test the plan or work item lists exists and was run; list each
  with its command and exit code.
- Every commit sha cited in the summary, a report, or a reply draft passes
  `git merge-base --is-ancestor <sha> HEAD`; re-cite after any rebase.
- Summary metadata (coverage numbers, file counts, verdicts) matches the
  summary's own text.
- Regenerated visual baselines/snapshots were opened and checked: not a
  loading skeleton, not blank, and showing the intended change. Record what
  you checked.
- Every required gate the plan names (e.g. the repo's preflight) was run, with
  its exit code recorded. The run is required; a failing preflight result is
  still handled as described above.
- Every must-address checklist item on the work item is done.

Write the summary as a `gc.build.implementation-summary.v1` artifact at a
bead-scoped path outside the repo's tracked tree:
`{{artifact_root}}/task-<source-anchor-id>-summary.md`, the path
`do-work`'s `close-source-anchor` targets. Resolve a relative artifact root
against the launcher rig root in `gc.work_dir`. Only if the artifact root is
blank or an unfilled placeholder, use
`$WORKTREE/.gc-artifacts/<source-anchor-id>/summary.md`; it must never be
committed. Record the absolute path on the workflow root bead as
`gc.implementation.summary_path` before closing. Never write it at the
worktree root, to a shared `.gc-artifacts/implementation-summary.md`, or to
the root rollup `implementation-summary.md` (gc-6svtga, gcas-j201cw).

The commit holds only the change itself: stage changed files by name, never
`git add -A` or `git add .`, and before committing confirm
`git diff --cached --name-only` lists no summary, report, or other workflow
artifact.

Include a Markdown coverage table. The validator only recognizes a table with
an `ID` column and a `Status` column. Use this shape:

| ID | Status |
| --- | --- |
| REQ-001 | covered |

Use mapping objects for front matter; do not use scalar shortcuts such as
`workflow: build-basic`. The top-level YAML shape must be:

- `schema: gc.build.implementation-summary.v1`
- `workflow: {id: <workflow-root-id>, formula: <root-workflow-formula>}`
- `methodology: {pack: gascity, name: build-basic}`
- `producer: {formula: do-work-item, stage: implement-item, attempt: <positive integer>}`
- `status: approved` or another schema-allowed status
- `trace: {upstream: [...], coverage: [...]}`

Trace front matter must use the validator shape exactly:

- `trace.upstream[]` entries must include `path` and `hash`; do not use
  `id`/`title`/`type` entries as the upstream shape.
- For the source anchor bead, use `path: beads/<source-anchor-id>` and
  `hash: bead:<source-anchor-id>`. For changed files or upstream build
  artifacts, use repo-relative paths and scheme-qualified hashes such as
  `sha256:<digest>` or `git:<revision>`.
- If an upstream entry lists `ids`, every listed id must appear exactly once in
  `trace.coverage` and in the Markdown coverage table with the same status.
- Coverage statuses are not artifact statuses. Use `covered` for satisfied
  requirements; do not use `approved` in `trace.coverage[].status` or the
  Markdown coverage table.
- A coverage entry with any status other than `covered` needs a `permit`
  beside its `rationale`: the sentence in the requirements artifact that
  allows leaving it open, quoted word for word, at least 20 characters, for
  example `permit: "The mayor checks this post-merge; it is not a worker AC."`.
  The gate checks the quote against the requirements artifact recorded on the
  workflow root (`gc.build.requirements_path`, fallback
  `gc.var.requirements_path`; for a convoy with no requirements artifact, the
  text of the work item) and rejects the summary when the permit is missing or
  its text is not there (gc-gdyaz). "Not run", "no environment", "left for
  review" and "left for publish" are not permits: a required check that has
  not been run is work still to do. Run it and record the result. If it truly
  cannot be done here, write the summary with `status: blocked` and say what is
  missing: a blocked summary tells review in plain terms that the work is not
  finished; it is not a way to pass. A requirement that was already satisfied
  before your change is `covered`, with the evidence.

Artifact validation: this step is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the summary recorded at `gc.implementation.summary_path` (fallbacks `gc.build.implementation_summary_path`, then `gc.var.summary_path`) against schema `gc.build.implementation-summary.v1`. Before closing this step, read the launcher rig root from the workflow root bead's `gc.work_dir`, then run the pinned pack's copy of the same validator locally from that rig root (the command resolves the gate script through gc's formula layers, exactly as the controller does) with `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`; fix every reported validation error before setting `gc.outcome=pass`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the summary in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the summary.
