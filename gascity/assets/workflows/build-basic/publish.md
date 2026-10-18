Use the built-in Gas City publish flow.

If publishing is enabled, publish the finalized build-basic result with the existing publish helper. If publishing is disabled, record a no-op publish outcome with the final artifact paths.

For build-basic, a finalized result can be an approved source anchor/worktree.
Do not mark publish failed or downgrade the workflow merely because the launcher
rig root was not mutated. When publishing is disabled, record a `noop` publish
result while preserving the approved build outcome.

`gc.outcome` is the workflow step outcome, not the publish mode. Never set
`gc.outcome=noop`. A disabled/no-op publish is a successful publish step:

```bash
gc bd update "$CLAIMED_BEAD_ID" \
  --set-metadata 'gc.outcome=pass' \
  --set-metadata 'gc.publish_outcome=noop' \
  --set-metadata 'gc.publish_mode=disabled' \
  --set-metadata 'gc.build_outcome=pass' \
  --set-metadata 'gc.final_report=<final report path>' \
  --set-metadata 'gc.artifact_root=<artifact root>'
gc bd close "$CLAIMED_BEAD_ID" --reason 'Publishing disabled; build-basic result approved.'
```

When push and open_pr are both enabled, the implementation step made only a
`[skip ci]` checkpoint push and opened no pull request (reviewer-first,
gc-gef2p8); this step runs CI, once. Push the approved branch head WITHOUT the
skip tag (an empty commit `chore: run CI` on top is acceptable when the head
commit carries it), open the pull request as a draft with the source bead id in
the title, wait for every required check on the head sha
(`pr-ci-green.sh --repo <owner>/<repo> --pr <n> --commit <sha>`, in manual
mode, must print `PASS`), and mark it ready (`gh pr ready <number>`) on green.
On a red required check, record the failing check names on the workflow root as
`gc.build.publish_ci_failures`, leave the draft a draft and hand the build back
to the fix loop for one more local round before publishing again; never retry
CI blind. When the remote branch already equals the approved commit (a
no-change hand-off), still push the empty `chore: run CI` commit if that commit
carries a skip-CI tag or has zero workflow runs, and mark ready only on `PASS`
for the PR's current head sha: zero check runs is not ready (gc-zb2a13). The
full protocol is in
`assets/workflows/build-from-review-base/publish.md`. When the build did not
finalize approved, open nothing.

Close only after the push, PR creation, or no-op publish result is recorded.
