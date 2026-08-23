Run the starter factory local-preflight review lane.

Unlike the other lanes, do not audit a recorded claim about testing — actually
run the rig's full local-CI-equivalent gate yourself, right now, and report
what you observed. This lane exists because a prose instruction asking an
implementation worker to run this honestly, and a reviewer to read that
recorded result, was verified live to be attention-dependent, not guaranteed:
one re-review caught a missing run, an identical re-review of a different item
did not. Running it here, independently, removes the dependence on anyone's
attention or self-report.

Before running anything, read `gc.build.code_review_context_path` from the
workflow root bead and use its `## Implementation Worktrees` section as the
authority for where commands must run. `gc.work_dir` is the launcher rig root,
not the implementation worktree. Do not run preflight from the launcher
checkout. If the context is missing a usable implementation worktree, write an
iterate finding against review setup instead of guessing a location.

Contract: `gc.work_dir` is the launcher rig root, not the implementation worktree.

For each implementation worktree listed in the review context:

1. `cd "$WORKTREE"` and verify `pwd -P` equals that worktree before running
   anything.
2. Run `make preflight-fast` if the worktree's Makefile defines that target,
   otherwise `make preflight`.
3. Record the exact command run, its exit code, and (on failure) enough of the
   tail output to act on.

Write concrete findings under the build artifact root. A failing worktree is a
real product defect, not missing evidence — do not describe it as missing
proof; describe what failed and where.

Close with `gc.outcome=pass`,
`code_review.preflight_verdict=approve|iterate`, and
`code_review.output_path=<preflight review report path>`.

Use explicit close metadata so the review loop can detect the lane result:

```bash
gc bd update "$CLAIMED_BEAD_ID" \
  --set-metadata 'gc.outcome=pass' \
  --set-metadata 'code_review.preflight_verdict=approve' \
  --set-metadata 'code_review.output_path=<preflight review report path>'
gc bd close "$CLAIMED_BEAD_ID" --reason 'Build-basic preflight review approved.'
```

If any worktree fails, set `code_review.preflight_verdict=iterate` instead of
`approve` and include the failing command, its exit code, and the relevant
output tail so the fix lane has something concrete to act on.

Do not set `code_review.verdict` or `code_review.report_path`; synthesis and
fix application own the final review verdict.

Do not invoke provider-native subagents. You are the starter factory preflight
review lane.
