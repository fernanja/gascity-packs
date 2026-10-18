This is the `build-base` publish stage. Treat it as a virtual contract that concrete formulas may override.

If `push` or `open_pr` is enabled, publish the finalized build result according to the workflow metadata. If publishing is disabled, record the exact reason and leave the artifacts ready for a later publisher.

Write a publish result artifact under the workflow artifact root when one is
available. Record the same publish outcome on the workflow root bead and this
publish step before closing.

Reviewer-first (gc-gef2p8): this is the only stage that runs CI. Implementation
and fix passes made only `[skip ci]` checkpoint pushes and opened no pull
request. Publish runs after `review` approved and `finalize` passed, and it:

1. pushes the approved branch head WITHOUT a skip-CI tag (an empty commit
   `chore: run CI` on top is acceptable when the head commit itself carries the
   tag);
2. opens the pull request as a draft, with the source bead id in the title;
3. waits for every required check on the head sha (`pr-ci-green.sh` in manual
   mode: `--repo <owner>/<repo> --pr <n> --commit <sha>`);
4. marks the pull request ready on green;
5. on a red required check, records the failing check names on the workflow
   root as `gc.build.publish_ci_failures`, leaves the pull request a draft and
   hands the build back to the fix loop for one more local round, then
   publishes again. It never retries CI blind.

The full protocol is in `assets/workflows/build-from-review-base/publish.md`.

Required workflow root metadata:

- `gc.build.publish_status=published|noop|failed` (`ci_red` while a red-check
  hand-back to the fix loop is in flight)
- `gc.build.publish_action=push|pr|push_pr|noop|failed`
- `gc.build.publish_recorded_at=<UTC timestamp>`
- `gc.build.publish_artifact_path=<publish result artifact path>`
- `gc.build.publish_reason=<short machine-readable reason>`
- `gc.build.publish_ci_failures=<failing required check names with URLs>`, only
  after a red required check

For disabled publishing, use `gc.build.publish_status=noop`,
`gc.build.publish_action=noop`, and a reason such as
`push=false_open_pr=false`. Also record whether remotes were present with
`gc.build.publish_remote_status`.

Close this step only after the publish action or explicit no-op is recorded on
both the workflow root and the publish step.
