
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

Hand off with green CI (gc-68exu). This workflow was launched with push
{{push}} and open_pr {{open_pr}}. When both are `true`, this step does not
end at the commit. It ends when the commit is on GitHub, in a draft pull
request, with every check green, so that review starts on a commit a machine
has already passed:

1. Push the work branch to `origin`. Let the repo's pre-push hooks run; never
   bypass them, and never push to the default branch.
2. Open a pull request for the branch against the default branch as a
   **draft**, with the source bead id in the title (the bead this build was
   dispatched for; the requirements artifact and the work item name it):
   `gh pr create --draft --base <default-branch> --head <branch> --title "<what changed> (<source-bead-id>)"`.
   If the branch already has an open pull request, use that one. Never open it
   non-draft and never mark it ready: a merge sweep merges any clean non-draft
   pull request, and this one has not been reviewed (gc-5gm0d: an
   implementation worker opened a ready pull request while review, repair and
   publish were all still open, and only a human converting it to draft kept
   it unmerged). Marking it ready belongs exclusively to the publish step,
   after review and repair-review approve.
3. Wait for GitHub's checks on that commit: `gh pr checks <number> --watch`.
   The command returns by itself when the checks finish, so it is not a
   blocking command of the kind the shell rules forbid. If your shell tool
   limits how long one call may run, run it in the background and wait for it
   to exit, or run it again until it returns by itself. Do not replace it with
   a fixed sleep or a long poll interval: a worker once sat an hour past the
   end of a run that way. If it says no checks are reported yet, CI has not
   registered; wait a minute and run it again.
4. A failed check is yours to fix before anyone reviews. Read the failed job's
   log (`gh run view <run-id> --log-failed`), fix the cause on the branch,
   commit, push, and wait again. A failing test is fixed; it is never skipped,
   quarantined or re-baselined away, and that includes a test that was already
   failing before your change. If the failure is in CI's own machinery (a
   runner that died, a download that timed out), you may rerun the failed job
   once (`gh run rerun <run-id> --failed`) and must record in the summary that
   you did, with the run URL and both results. The gate does not tell a flake
   from a defect: a check that is still red is red.
5. Before closing, run the gate yourself from the launcher rig root, the same
   script the controller runs when this step closes:
   `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/pr-ci-green.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`.
   It must print `PASS` for the commit at your worktree's `HEAD`. Record the
   pull request URL, the head sha and that `PASS` line in the summary's
   `## Verification`. A criterion such as "the pull request's checks are
   green" is then `covered`, with the run as evidence.

When push or open_pr is not `true`, do not open a pull request; the gate then
records `skipped: no publishing intent` and passes. If the branch has an open
pull request anyway, the gate still requires its checks to be green.

The handoff gate is `../assets/scripts/checks/implementation-handoff-valid.sh`:
the artifact validator described below, then
`../assets/scripts/checks/pr-ci-green.sh`. The controller does not wait for
CI. A step closed while a check is unfinished or red fails the gate and comes
back to you as a new attempt with the failing checks in `gc.attempt_log`;
after three attempts the step fails and review does not start.

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
- Every must-address checklist item on the work item is done.

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
- A coverage entry with any status other than `covered` needs a `permit`
  beside its `rationale`: the sentence in the requirements artifact that
  allows leaving it open, quoted word for word, at least 20 characters, for
  example `permit: "The mayor checks this post-merge; it is not a worker AC."`.
  The gate checks the quote against the requirements artifact recorded on the
  workflow root (`gc.build.requirements_path`, fallback
  `gc.var.requirements_path`; for a convoy with no requirements artifact, the
  text of the work item) and rejects the summary when the permit is missing or
  its text is not there (gc-gdyaz). "Not run", "no environment", "left for
  review" and "left for publish" are not permits: a required check that has
  not been run is work still to do. Run it and record the result. If it truly
  cannot be done here, write the summary with `status: blocked` and say what is
  missing: a blocked summary tells review in plain terms that the work is not
  finished; it is not a way to pass. A requirement that was already satisfied
  before your change is `covered`, with the evidence.

Artifact validation: this step is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the summary recorded at `gc.implementation.summary_path` (fallbacks `gc.build.implementation_summary_path`, then `gc.var.summary_path`) against schema `gc.build.implementation-summary.v1`. Before closing this step, read the launcher rig root from the workflow root bead's `gc.work_dir`, then run the pinned pack's copy of the same validator locally from that rig root (the command resolves the gate script through gc's formula layers, exactly as the controller does) with `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`; fix every reported validation error before setting `gc.outcome=pass`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the summary in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the summary.
