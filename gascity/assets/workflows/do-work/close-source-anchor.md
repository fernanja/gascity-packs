
Resolve `<source-anchor-id>` using the same rules as `prepare-worktree`. Read `work_dir` from the source anchor and verify the implementation commit and
summary evidence are present in that worktree. Write per-item summary to
{{summary_path}} when set. If `summary_path` is not set, first use
`gc.implementation.summary_path` from the preceding implementation step when it
is present; otherwise use `{{artifact_root}}/task-<source-anchor-id>-summary.md`.

When reading beads with `gc bd show --json`, handle both an object and a
one-element list before reading metadata. `gc.work_dir` is the launcher rig
root, not the implementation worktree. If the source anchor `work_dir` is
missing or equals the launcher root, fail this step instead of closing the
source anchor.

If the worktree does not contain the recorded implementation commit, do not
fail immediately: a squash-merge repo discards the pre-squash commit SHA on
merge, so "commit not found" alone does not mean the work is missing. Search
for a MERGED pull request referencing `<source-anchor-id>` (title, body, or
branch name — same pattern as `merge-prs.md`'s bead-reference search, e.g.
`gh pr list --repo <repo> --state merged --search '<source-anchor-id>'`). If
a matching merged PR is found, treat the source anchor as verified: use the
PR's actual merge commit (not the pre-squash SHA) as the verified
implementation commit and proceed. Only fail this step when the worktree
lacks the commit AND no matching merged PR exists.

On success, close only `<source-anchor-id>` with `gc.outcome=pass`. Include the
verified commit and summary path in the source-anchor close reason. Read the
source anchor back with `gc bd show <source-anchor-id> --json` and verify
`status=closed` and `gc.outcome=pass`; if either check fails, fix the source
anchor before closing this step. Do not close this step with pass while the source anchor remains open. Then close this step. Do not close the drain-unit
convoy, parent convoy, or broader workflow root from this step.
