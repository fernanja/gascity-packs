This is the `implementation-item-base` methodology contract shared-drain item
step.

Concrete methodology packs override this step to perform one single-lane item
of implementation work with optional context from `{{context_path}}`. Do not
create parallel work inside this shared-drain contract.

Default fallback behavior must still enforce the worktree contract: resolve the
source anchor from workflow metadata, read the authoritative worktree from the
source anchor, and `cd "$WORKTREE"` before source reads, edits, tests, hashes,
or commits. `gc.work_dir` is the launcher rig root, not the implementation
worktree. When reading beads with `gc bd show --json`, handle both an object and a
one-element list before reading metadata.

The launcher checkout's `main` is never a valid commit target, including under
pressure to unblock validation. If verification needs code that appears
unreachable from `main` (an earlier item's commit orphaned, a sibling
worktree's work not yet merged), that is a signal to re-resolve `$WORKTREE`/the
source anchor or fail the step with a clear diagnostic — not license to
cherry-pick, merge, or otherwise commit anything onto the shared rig root's
checked-out branch to make validation pass locally. The only path from a
worktree to `main` is a GitHub PR through the pack's normal publish step;
nothing before that step may write to `main` directly, for any reason.

This step must not open a GitHub pull request itself, draft or otherwise —
only push the implementation branch to the remote so downstream review,
repair-review, and the publish step (or CI) can see it. Opening a PR here,
even in draft, gives it a head branch and a mergeable state before review or
repair-review has run; an unrelated automated merge sweep that merges any
clean non-draft PR does not know this workflow is still mid-review and can
squash-merge unreviewed work the moment CI goes green (confirmed incident,
gc-5gm0d: a shared-drain item worker opened a PR while implement/review/
repair-review/publish were all still open, and it was only kept unmerged
because a human manually converted it to draft). PR creation and readiness
belong exclusively to the publish step, after review and repair-review
approve.

Write the per-item implementation summary as a
`gc.build.implementation-summary.v1` artifact at a bead-scoped path outside
the repo's tracked tree:
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

The summary body must contain these exact schema-required `##` headings in this
order:

- `## Summary`
- `## Intended Behavior`
- `## Changed Files`
- `## Verification`
- `## Remaining Risks`

Artifact validation: this step is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the summary recorded at `gc.implementation.summary_path` (fallbacks `gc.build.implementation_summary_path`, then `gc.var.summary_path`) against schema `gc.build.implementation-summary.v1`. Before closing this step, read the launcher rig root from the workflow root bead's `gc.work_dir`, then run the pinned pack's copy of the same validator locally from that rig root (the command resolves the gate script through gc's formula layers, exactly as the controller does) with `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`; fix every reported validation error before setting `gc.outcome=pass`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the summary in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the summary.
