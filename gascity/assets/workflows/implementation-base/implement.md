This is the `implementation-base` methodology contract implementation step.

Concrete methodology packs override this step to apply their native
implementation discipline. Work only inside the prepared worktree and preserve
the source anchor for the close step.

Default fallback behavior must still enforce the worktree contract: resolve the
source anchor from workflow metadata, read `work_dir` from that source anchor,
and `cd "$WORKTREE"` before source reads, edits, tests, hashes, or commits.
`gc.work_dir` is the launcher rig root, not the implementation worktree. When
reading beads with `gc bd show --json`, handle both an object and a one-element
list before reading metadata.

The launcher checkout's `main` is never a valid commit target, including under
pressure to unblock validation. If verification needs code that appears
unreachable from `main` (an earlier item's commit orphaned, a sibling
worktree's work not yet merged), that is a signal to re-resolve `$WORKTREE`/the
source anchor or fail the step with a clear diagnostic — not license to
cherry-pick, merge, or otherwise commit anything onto the shared rig root's
checked-out branch to make validation pass locally. The only path from a
worktree to `main` is a GitHub PR through the pack's normal publish step;
nothing before that step may write to `main` directly, for any reason.

Write the per-item implementation summary as a
`gc.build.implementation-summary.v1` artifact at a bead-scoped path outside
the repo's tracked tree: `{{summary_path}}` when set, else
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

Artifact validation: this step is gated by `.gc/scripts/checks/build-artifact-valid.sh`, which validates the summary recorded at `gc.implementation.summary_path` (fallbacks `gc.build.implementation_summary_path`, then `gc.var.summary_path`) against schema `gc.build.implementation-summary.v1`. Before closing this step, read the launcher rig root from the workflow root bead's `gc.work_dir`, then run the same validator locally from that rig root with `GC_BEAD_ID=<claimed-step-id> .gc/scripts/checks/build-artifact-valid.sh`; fix every reported validation error before setting `gc.outcome=pass`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the summary in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the summary.
