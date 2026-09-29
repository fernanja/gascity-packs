Apply build-basic starter review findings.

Use implementation target {{implementation_target}} for any code changes. Read
the starter review synthesis. If all four review lanes approve, write a no-op
review summary. If required fixes or missing evidence remain, make the smallest
focused changes, run the relevant proof commands, and write the review-fix
summary under the build artifact root. For a preflight iterate specifically,
the fix is whatever `make preflight`/`make preflight-fast` reported failing —
fix the code, then re-run it yourself in the worktree before writing the
summary; do not just describe the failure back.

Before writing the review-fix summary, run the implementation self-check and
record it there. It fails closed: the checks the plan lists were run (command
and exit code); every cited commit sha passes
`git merge-base --is-ancestor <sha> HEAD`; summary metadata matches its own
text; regenerated baselines/snapshots were opened and show the intended
change; required gates (e.g. preflight) were run with exit codes recorded.

Wording-only findings touch only prose (docs, reply drafts, reports, code
comments) and change no code or test behaviour. Fix them in the same pass as
the code findings. When the synthesis's only remaining findings are
wording-only, fix them, re-read each result against its finding, confirm this
pass's diff changes only prose, record that validation in the review-fix
summary, and set `code_review.verdict=done` instead of starting another review
loop. A pass that changes code or test behaviour always sets `iterate` so the
lanes re-review it; this rule never skips that.

A fix for an intermittent or timing failure needs the pre-fix code failing
and the post-fix code passing under the same reproduction method. If a finding
is marked `cannot_reproduce`, or you cannot make the pre-fix code fail, do not
guess-fix: record `blocked` and the evidence in the review-fix summary, then
close with `gc.outcome=fail`, `gc.failure_class=hard` (terminal; it stops the
loop) and `gc.failure_reason=cannot_reproduce`. The mayor owns the next step.

Apply fixes to the implementation source anchor/worktree named in the review
context, not to the launcher rig root. An unchanged root checkout is not itself
a required fix for build-basic; publish owns propagation beyond the source
anchor. If the only reported issue is "implementation exists in the worktree but
not the root checkout" and the source anchor/worktree passes the requirements,
record a no-op fix summary and set `code_review.verdict=done`.

Before editing or running proof commands, read `gc.build.code_review_context_path`
from the workflow root bead and use its `## Implementation Worktrees` section as
the authority for writable code. `gc.work_dir` is the launcher rig root, not the
implementation worktree. Do not inspect or edit the launcher checkout. Select
the implementation worktree for each finding from the source anchor/worktree
recorded in the review context, run `cd "$WORKTREE"`, and verify `pwd -P` equals
that worktree before making changes. Resolve all relative paths in synthesis
findings against the selected worktree. If a required fix cannot be tied to an
implementation worktree, write an iterate summary explaining the missing
worktree evidence and do not patch the launcher root. If multiple worktrees are
listed and a finding is ambiguous, leave it as iterate until the owning worktree
is explicit.

Contract: `gc.work_dir` is the launcher rig root, not the implementation worktree.

Set `code_review.verdict=done` only when acceptance, test evidence,
simplicity, and preflight all approve after this pass, or under the
wording-only rule above. Set `code_review.verdict=iterate` when required fixes
remain.

Except for the `cannot_reproduce` stop above, always close with
`gc.outcome=pass`,
`code_review.verdict=done|iterate`,
`code_review.report_path=<starter review summary path>`, and
`code_review.output_path=<starter review summary path>`.

Use the exact claimed bead id when updating metadata. Do not pass freeform notes
or additional positional arguments to `gc bd update`; unquoted words can resolve to
unrelated beads. Use this command shape:

```bash
gc bd update "$CLAIMED_BEAD_ID" \
  --set-metadata 'gc.outcome=pass' \
  --set-metadata 'code_review.verdict=done' \
  --set-metadata 'code_review.report_path=<starter review summary path>' \
  --set-metadata 'code_review.output_path=<starter review summary path>'
gc bd close "$CLAIMED_BEAD_ID" --reason 'Build-basic starter review approved.'
```

Do not invoke provider-native subagents. This starter factory graph lane is the
fix delegation mechanism.
