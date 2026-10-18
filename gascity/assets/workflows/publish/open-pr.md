
If open_pr is {{open_pr}}, create a PR only after push succeeds and sanitized
title/body from final report {{final_report}} pass policy.

Reviewer-first (gc-gef2p8): open the PR as a draft (`gh pr create --draft`),
with the source bead id in the title, and reuse an open PR for the branch if
one exists. Then wait for every required check on the pushed head sha and judge
it with `pr-ci-green.sh --repo <owner>/<repo> --pr <n> --commit <sha>` in
manual mode (`PASS` is green; the pinned script reads check runs and commit
statuses in both shapes GitHub returns). On `PASS`, mark the PR ready
(`gh pr ready <number>`). On a red required check, record the failing check
names with their URLs on the workflow root as `gc.build.publish_ci_failures`,
leave the PR a draft, and report the helper as failed with reason `ci_red` so
the calling build can hand back to its fix loop for one more local round and
publish again. Never retry CI blind.
