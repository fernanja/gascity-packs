
If push {{push}} or open_pr {{open_pr}} are explicit opt-ins, publish only
after the direct implementation summary has completed successfully. Resolve the
publish report from summary_path {{summary_path}} when set; otherwise read the
`gc.implementation.summary_path` value recorded by the summarize step in
workflow root metadata. Fail closed if no absolute summary report path is
available.

Direct implement does not run gap-analysis or review loops. Treat this as an
explicit caller authorization to publish the direct implementation result, and
use the same protected-branch, lease-safe push, sanitized PR title/body, and
collision checks as the pack publish helper. When push and open_pr are both
opt-ins, the implementation step has made only a `[skip ci]` checkpoint push and
opened no pull request (reviewer-first, gc-gef2p8): this step runs CI, once.
Push the commit being published WITHOUT the skip tag (an empty commit
`chore: run CI` on top is acceptable when it carries the tag), open the pull
request as a draft with the source bead id in the title, wait for every
required check on the head sha (`pr-ci-green.sh --repo <owner>/<repo> --pr <n>
--commit <sha>` in manual mode must print `PASS`), and mark it ready
(`gh pr ready <number>`) on green. A red required check is reported, not
retried: record the failing check names on the workflow root as
`gc.build.publish_ci_failures`, leave the draft a draft and close this step
`gc.outcome=fail`, `gc.failure_class=hard`. When the remote branch already
holds the commit being published, push is still required if that commit
carries a skip-CI tag or has zero workflow runs (`gh run list --commit <sha>`);
never mark ready on "no failures", only on `PASS` for the PR's current head
sha (gc-zb2a13). If neither push nor open_pr is an
explicit opt-in, close with `gc.outcome=pass` without mutating remotes.
