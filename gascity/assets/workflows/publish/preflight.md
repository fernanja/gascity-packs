
Validate auth, token scope, remote, branch names, PR base, collision policy,
protected/default targets, sanitized metadata, final report {{final_report}},
push authorization {{push}}, and open_pr authorization {{open_pr}}.

Reviewer-first (gc-gef2p8): this helper runs only after the review approved the
build and finalize passed, and it is the only place CI runs. Confirm
`gc.build.review_subject_commit` is recorded on the workflow root and that the
approving review is the latest; fail closed otherwise. Earlier pushes of the
branch were `[skip ci]` checkpoints, so no pull request exists yet on a first
publish.
