
Resolve `<source-anchor-id>` using the same rules as `prepare-worktree`. For a
synthetic drain-unit convoy, the source anchor is the original drain member in
`gc.drain_member_id`, not the synthetic convoy id. Read `work_dir` from the source anchor, never read `work_dir` from the synthetic drain-unit convoy,
validate that it is an absolute existing git worktree, set `WORKTREE` to that
path, then `cd "$WORKTREE"` before reading or editing source files. If
`work_dir` is missing, invalid, or points at the launcher checkout, fail this step before editing.

Do not infer the source anchor from dependency ids such as the
`prepare-worktree` step. Read the claimed step bead's `gc.root_bead_id`, read
that do-work root with `gc bd show <root-bead-id> --json`, then read the root
metadata `gc.input_convoy_id`. Read that input convoy with `gc bd show
<input-convoy-id> --json`; if the JSON output is a one-element list, unwrap the
first element before reading metadata. If the input convoy has
`gc.synthetic_kind=drain-unit-convoy`, use its `gc.drain_member_id` as the
source anchor. Otherwise use the input convoy id as the source anchor. Then
read the source anchor and use only its `work_dir` metadata as `WORKTREE`.

`gc.work_dir` is the launcher rig root, not the implementation worktree. Use
`gc.work_dir` only later to run the artifact validator (see Artifact validation below).
After resolving `WORKTREE`, run `cd "$WORKTREE"` and verify `pwd -P` equals
`$WORKTREE` before any source read, source edit, test, file hash, `git add`, or
`git commit`. If a command uses the launcher checkout path for source edits,
verification, hashes, or commits, the step is invalid and must fail.

Do not edit files in the launcher checkout. Implement only the owned source
anchor boundary, run sandboxed verification from inside the worktree, and make a
focused commit in the worktree. Leave the source anchor open for
`close-source-anchor`; close only this implementation step when done.

The launcher checkout's `main` is never a valid commit target, including
under pressure to unblock validation. If verification needs code that
appears unreachable from `main` (an earlier item's commit orphaned, a
sibling worktree's work not yet merged), that is a signal to re-resolve
`WORKTREE`/the source anchor or fail the step with a clear diagnostic — not
license to cherry-pick, merge, or otherwise commit anything onto the shared
rig root's checked-out branch to make validation pass locally. The only path
from a worktree to `main` is a GitHub PR through the pack's normal publish
step; nothing before that step may write to `main` directly, for any reason.

Requirements amended after planning (requirements_changed_after_plan). Before
you start, read the requirements path from the workflow root
(`gc.build.requirements_path`, fallback `gc.var.requirements_path`) and the
plan's `trace.upstream` entry for that file (the plan is at `gc.build.plan_path`,
fallback `gc.var.plan_path`). Compare the file as it is now with the version
the plan recorded: recompute the hash with the plan's scheme
(`shasum -a 256 <requirements path>` against a `sha256:<digest>` entry), or
compare the file's `updated_at` with the one the plan recorded. When they
differ, the plan was written against other requirements: do not implement
against the stale plan. Mail the mayor with both values (`gc mail send mayor -s
"requirements changed after plan: <workflow-root-id>" -m "<requirements path>,
plan recorded <recorded hash or updated_at>, file now <current hash or
updated_at>"`). Record the stop on the workflow root with
`gc.build.status=blocked`, `gc.build.blocked_step=<this step id>` and
`gc.failure_class=requirements_changed_after_plan`, then close this step as
blocked and not retryable (a retry reads the same two files):
`gc bd update "<claimed-step-id>" --set-metadata gc.outcome=fail --set-metadata gc.failure_class=hard --set-metadata "gc.failure_reason=requirements_changed_after_plan: <both values>"`
and `gc bd close "<claimed-step-id>"`. When no plan or no requirements path is
recorded, or the plan records neither a hash nor an `updated_at` you can
recompute, say so in the summary and continue: only a recorded mismatch stops
the step.

Hand off by local commit (reviewer-first, gc-gef2p8). This workflow was
launched with push {{push}} and open_pr {{open_pr}}. CI does not run before
review: the reviewer works from your local commit, in a scratch worktree of the
same repository, and CI runs once, on the commit the reviewer approved, in the
publish step. When both are `true`, this step ends with a commit, a recorded
hand-off and one checkpoint push. It does not open a pull request and it does
not wait for CI:

1. Run the local pass before the commit: `make preflight-fast` (or the rig's
   documented fast gate) and the tests of the code you changed. A failing test
   is fixed; it is never skipped, quarantined or re-baselined away. Do not run
   the full browser suite or the real-server suites here: the reviewer verifies
   at the rig's configured breadth (`review_verification_scope`), and CI and the
   weekly run cover the full matrix.
2. Commit on the work branch in your worktree. The subject of the hand-off
   commit (the head of the branch) carries the tag `[skip ci]`, for example
   `fix: tap rows on phones [skip ci]`. GitHub skips the push and
   pull_request workflows for a head commit with that tag, and the branch's
   required checks stay pending, so nothing can merge it before the publish
   step.
3. Make exactly one checkpoint push, for durability: `git push origin
   <work-branch>`, fast-forward only, never to the default branch. Let the
   repo's pre-push hooks run; never bypass them. Do not run `gh pr create`: no
   pull request exists until the publish step opens one.
4. Record the hand-off keys on this workflow's root (`gc.root_bead_id` on your
   claimed step bead), after the push so the sha is final:
   `gc bd update "<workflow-root-id>" --set-metadata "gc.build.handoff_commit=$(git rev-parse HEAD)" --set-metadata "gc.build.handoff_branch=<work-branch>"`.
   Both keys, on the root, every time: a correct checkpoint on origin with no
   keys recorded is a failed hand-off (gc-jhuzd0: two of the first four items
   did exactly this and the summarize step then bridged nothing). The
   reviewer's subject is exactly that sha; the local worktree is shared git
   storage, so every session on this machine can read it.
5. Before closing, run the gate yourself from the launcher rig root, the same
   script the controller runs when this step closes:
   `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/pr-ci-green.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`.
   With no pull request yet it prints `reviewer-first: CI deferred to publish`
   and exits 0. Record the commit sha, the branch, that line and the local
   pass (each command with its exit code) in the summary's `## Verification`.
   A criterion such as "CI is green" is `covered` only by a later step; say
   so in `## Remaining Risks` instead of claiming it.
6. Close the step only after the keys read back. Run the hand-off key gate
   the same way, from the launcher rig root:
   `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/handoff-commit-ancestor.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`.
   It must print `handoff-commit-ancestor: PASS` (or `skipped:` when push or
   open_pr is not `true`). A `FAIL` names the two `gc bd update` calls that
   record `gc.build.handoff_commit` and `gc.build.handoff_branch`: run them,
   run the gate again, and only then close the step.

If the local pass cannot be made green, or the fix needs a decision that is not
yours, do not close the step as passed, do not weaken, skip or re-baseline a
test, and do not close it over and over to use up the attempts. Write what you
found in the summary, then close the step as a hard failure that names each
failing command:
`gc bd update "<claimed-step-id>" --set-metadata gc.outcome=fail --set-metadata gc.failure_class=hard --set-metadata "gc.failure_reason=<commands and results>"`,
then `gc bd close "<claimed-step-id>"`. `gc.failure_class=hard` ends the step
at once and review does not start; a failed outcome without it is handed back
as a new attempt, up to three times.

When push or open_pr is not `true`, make no checkpoint push and open no pull
request; the gate then records `skipped: no publishing intent` and passes.

The handoff gate is `../assets/scripts/checks/implementation-handoff-valid.sh`:
the artifact validator described below, then
`../assets/scripts/checks/handoff-commit-ancestor.sh` (the keys), then
`../assets/scripts/checks/pr-ci-green.sh`. The controller does not wait for CI
and there is none to wait for. A step closed with no `gc.build.handoff_commit`
or no `gc.build.handoff_branch` recorded on the root, or with a recorded commit
that is not the head of your worktree,
fails the gate and comes back as a new attempt with the reason in
`gc.attempt_log`; after three attempts the step fails and review does not
start. GitHub or the bead store being unreachable is not an attempt: the gate
retries, and says `INFRA` rather than `FAIL`.

Write or update the task summary with these schema-required body sections,
using the exact `##` headings below in this order:

- `## Summary`
- `## Intended Behavior`
- `## Changed Files`
- `## Verification`
- `## Remaining Risks`

The `## Verification` section must include both the first verification command
and the final proof command, with the observed pass/fail result.

Before closing, run this self-check and record it under `### Self-Check`
inside `## Verification`. It fails closed: if any item does not hold, fix it
and re-check before closing. It must never be left for review to find.

- Every check/test the plan or work item lists exists and was run; list each
  with its command and exit code.
- Every commit sha cited in the summary, a report, or a reply draft passes
  `git merge-base --is-ancestor <sha> HEAD`; re-cite after any rebase.
- Summary metadata (coverage numbers, file counts, verdicts) matches the
  summary's own text.
- Regenerated visual baselines/snapshots were opened and checked: not a
  loading skeleton, not blank, and showing the intended change. Record what
  you checked.
- Every required gate the plan names (e.g. the repo's preflight) was run, with
  its exit code recorded.
- Every must-address checklist item on the work item (MA-n, V-n, or the
  item's own numbered list) is done AND listed here by id with its evidence
  pointer: the test name or file:line that proves it, and the command plus
  exit code that ran it. An item with no pointer fails this check; a
  'shorter reading' of the item is not a disposition (gc-4ia4s5: a summary
  reported two tests and no red run against an item naming three tests and
  a deterministic failure; review caught it a round later).

Write the summary as a `gc.build.implementation-summary.v1` artifact at a
bead-scoped path outside the repo's tracked tree: `{{summary_path}}` when set,
else `{{artifact_root}}/task-<source-anchor-id>-summary.md`, the path
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

Include a Markdown coverage table. The validator only recognizes a table with
an `ID` column and a `Status` column. Use this shape:

| ID | Status |
| --- | --- |
| REQ-001 | covered |

Use mapping objects for front matter; do not use scalar shortcuts such as
`workflow: build-basic`. The top-level YAML shape must be:

- `schema: gc.build.implementation-summary.v1`
- `workflow: {id: <workflow-root-id>, formula: <root-workflow-formula>}`
  `workflow.id` is THIS workflow's root bead id: the `gc.root_bead_id` value on
  the claimed step bead, read now, never a value copied from another work
  item's summary file. The gate passes that id to the validator and rejects
  any other (gc-d09g6: task-gcas-onsl2r-summary.md named the previous item's
  root gcas-o8gynk, and only the consumer noticed).
- `methodology: {pack: gascity, name: build-basic}`
- `producer: {formula: do-work, stage: implement, attempt: <positive integer>}`
- `status: approved` or another schema-allowed status
- `trace: {upstream: [...], coverage: [...]}`

Trace front matter must use the validator shape exactly:

- `trace.upstream[]` entries must include `path` and `hash`; do not use
  `id`/`title`/`type` entries as the upstream shape.
- For the source anchor bead, use `path: beads/<source-anchor-id>` and
  `hash: bead:<source-anchor-id>`. For changed files or upstream build
  artifacts, use repo-relative paths and scheme-qualified hashes such as
  `sha256:<digest>` or `git:<revision>`.
- If an upstream entry lists `ids`, every listed id must appear exactly once in
  `trace.coverage` and in the Markdown coverage table with the same status.
- Coverage statuses are not artifact statuses. Use `covered` for satisfied
  requirements; do not use `approved` in `trace.coverage[].status` or the
  Markdown coverage table.
- Every requirement id the requirements artifact defines needs a coverage
  entry, whether or not you list it under `ids`. The gate reads the ids from
  the requirements file itself: labels such as `AC-1`, `SCOPE-2`, `REQ-3` that
  lead a list item, a heading or a paragraph, except under a heading that says
  out of scope, non-goals, open questions, background or verified. A summary
  that leaves one out of both lists is rejected; other labels (`OQ-`, `OOS-`,
  `CON-`) need an entry only if you list them. The one exception is a build
  split into several work items: each item's summary covers what its own work
  item delivers, and the gate does not ask it for every id.
- A coverage entry with any status other than `covered` needs a `permit`
  beside its `rationale`: the sentence in the requirements artifact that
  hands the requirement off, quoted word for word, at least 20 characters, for
  example `permit: "The mayor checks this post-merge; it is not a worker AC."`.
  The quote must itself say the requirement is for later, for someone else, or
  not for this work ("post-merge", "out of scope", "not in this work",
  "follow-up bead", "mayor-owned", "deferred" and the like): the requirement's
  own statement is in the requirements too, and it is not a permit. The gate
  checks the quote against the requirements artifact recorded on the workflow
  root or, for an implementation item, on the workflow that launched it
  (`gc.build.requirements_path`, fallback `gc.var.requirements_path`) and
  rejects the summary when the permit is missing, its text is not there, or it
  hands nothing off (gc-gdyaz). If no root records a requirements artifact,
  the gate says so and names the command that records it; it never checks a
  quote against the work item. A conditional requirement ("If only the test is
  wrong: ...") whose condition does not hold is `not_applicable`: its `permit`
  quotes the requirement's own conditional clause and its `rationale` says why
  the condition is false. "Not run", "no environment", "left for
  review" and "left for publish" are not permits: a required check that has
  not been run is work still to do. Run it and record the result. If it truly
  cannot be done here, write the summary with `status: blocked` and say what is
  missing: a blocked summary tells review in plain terms that the work is not
  finished; it is not a way to pass. A requirement that was already satisfied
  before your change is `covered`, with the evidence.

Artifact validation: this step is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the summary recorded at `gc.implementation.summary_path` (fallbacks `gc.build.implementation_summary_path`, then `gc.var.summary_path`) against schema `gc.build.implementation-summary.v1`. Before closing this step, read the launcher rig root from the workflow root bead's `gc.work_dir`, then run the pinned pack's copy of the same validator locally from that rig root (the command resolves the gate script through gc's formula layers, exactly as the controller does) with `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`; fix every reported validation error before setting `gc.outcome=pass`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the summary in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the summary. The schema id of this artifact is `gc.build.implementation-summary.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.implementation-summary.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).
