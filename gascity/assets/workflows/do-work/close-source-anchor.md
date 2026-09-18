
Resolve `<source-anchor-id>` using the same rules as `prepare-worktree`. Read `work_dir` from the source anchor.

Before closing the source anchor, copy any implementation evidence out of the
worktree to a durable location, since the worktree reaper can remove
`work_dir` as soon as the source anchor closes:

- Resolve the current evidence path: `gc.implementation.summary_path`
  (fallback `gc.build.implementation_summary_path`) on the workflow root,
  when present; otherwise there is no prior evidence file yet.
- Resolve and canonicalize the durable destination: `{{summary_path}}` when
  the caller set it, else `{{artifact_root}}/task-<source-anchor-id>-summary.md`.
  Canonicalize the resolved candidate (resolve symlinks/`..`, e.g.
  `realpath` on the parent directory) and reject it if it falls under
  `$GC_CITY/.gc/worktrees/` — the city's managed worktree root, not just the
  current source anchor's `work_dir` — since any worktree under that root can
  be reaped independently of this one. Apply this same check to the
  `{{artifact_root}}` fallback itself: if the fallback also resolves under
  the managed worktree root, fail this step with a configuration error
  instead of silently writing evidence somewhere the reaper can still remove
  it.
- If the current evidence path is a descendant of `work_dir`: verify it is
  present, then copy it byte-for-byte to the validated durable destination
  (create parent dirs under `{{artifact_root}}`), verify the copy exists and
  matches the source size, then update the WORKFLOW ROOT (resolve via
  `gc.root_bead_id` on this step bead):
  `gc bd update <root-bead-id> --set-metadata "gc.implementation.summary_path=<destination>"`,
  and repoint `gc.build.implementation_summary_path` too if it also pointed
  at the worktree-local path.
- If the current evidence path is already outside any worktree: verify it is
  present at its recorded path and record it as-is (unchanged behavior).
- If no current evidence path was resolved: write a fresh minimal summary
  directly at the validated durable destination (unchanged fallback, now
  explicit that the destination is always durable).

Separately, verify the implementation commit is present in the worktree (see
the squash-merge fallback below if it is not).

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
