
Run inside an existing shared worktree lifecycle. Resolve reserved `convoy_id`,
read `gc.drain_member_id` and `gc.drain_item_index`, validate ownership and
verification policy, validate context path {{context_path}} when set, implement
the item, write an item summary, and close only the source anchor on success.

Do not infer the source anchor from dependency ids. Read the reserved convoy and
source anchor metadata directly; when `gc bd show --json` returns a one-element
list, unwrap the first element before reading metadata. `gc.work_dir` is the
launcher rig root, not the implementation location. Use the authoritative
worktree recorded on the source anchor, run `cd "$WORKTREE"`, and verify
`pwd -P` equals `$WORKTREE` before any source read, source edit, test, file
hash, `git add`, or `git commit`.

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
3. Wait for GitHub's checks on that commit, with a bound:
   `perl -e '$t=shift; $p=fork; exec @ARGV unless $p; $SIG{ALRM}=sub{kill "TERM",$p; exit 124}; alarm $t; waitpid $p,0; exit($? & 127 ? 128+($? & 127) : $?>>8)' 1500 gh pr checks <number> --watch --interval 30`.
   This `perl` wrapper is the time limit that works on macOS and Linux alike
   (GNU `timeout` is not installed on macOS, and a bare `alarm` before `exec`
   does not stop `gh`, which ignores that signal). The command returns by
   itself when the checks finish (0: green, 1: a check failed) and the wrapper
   ends it after 25 minutes (status 124), so it is not a blocking command of
   the kind the shell rules forbid. Any other status (128 or more: `gh` was
   ended by a signal) means the wait did not finish: run it again. If your shell tool allows less than that for one
   call, pass a smaller number of seconds, or run it in the background and
   wait for it to exit. Do not replace it with a fixed sleep or a long poll
   interval: a worker once sat an hour past the end of a run that way. If it
   says no checks are reported yet, CI has not registered; wait a minute and
   run it again. When the wrapper ends the wait, run `gh pr checks <number>`
   once and read what is unfinished. A check that is running gets another
   bounded wait, three waits at most. A check that sat `queued` or `waiting`
   through a whole wait is stuck: do not wait again, take the exit under "If
   CI cannot be made green" below.
4. A failed check is yours to fix, before anyone reviews, when your branch
   broke it. Read the failed job's log (`gh run view <run-id> --log-failed`),
   fix the cause on the branch, commit, push, and wait again. A failing test
   is fixed; it is never skipped, quarantined or re-baselined away. A failure
   the base branch also has is different: when the same job of the same
   workflow is red on the base branch's own most recent run of it, at the same
   step, and the base branch does not require that check, the gate lets it
   pass and prints a `WARNING` line naming both runs and the step. A job that
   fails at a step where the base branch's job passed is yours, whatever else
   is red on the base branch. A red check from another app (a deployment
   preview such as Vercel) that the base branch does not require passes with a
   `WARNING` as well. Do not fix the base branch's failure on this branch, and
   do not pass over it in silence: copy each `WARNING` line, with its URLs,
   into the summary's `## Remaining Risks`. The gate makes this call, not you:
   a red check that the base branch requires, that is green on the base
   branch, or that the base branch has never run, blocks. If the gate says the
   pull request has merge conflicts, merge the base branch into the work
   branch, resolve them, push, and wait again: GitHub runs no pull request
   checks on a conflicted pull request. If the failure is in CI's own
   machinery (a runner that died, a download that timed out), you may rerun
   the failed job once (`gh run rerun <run-id> --failed`) and must record in
   the summary that you did, with the run URL and both results. The gate does
   not tell a flake from a defect: a check that is still red is red.
5. Before closing, run the gate yourself from the launcher rig root, the same
   script the controller runs when this step closes:
   `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/pr-ci-green.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`.
   It must print `PASS` for the commit at your worktree's `HEAD`. Record the
   pull request URL, the head sha and that `PASS` line in the summary's
   `## Verification`. A criterion such as "the pull request's checks are
   green" is then `covered`, with the run as evidence.

If CI cannot be made green: when a check your branch broke is still red after
your fixes, a check is stuck, or the fix needs a decision that is not yours,
do not close the step as passed, do not weaken, skip or re-baseline a test,
and do not close it over and over to use up the attempts. Write what you found
in the summary, then close the step as a hard failure that names each failing
or stuck check with its URL:
`gc bd update "<claimed-step-id>" --set-metadata gc.outcome=fail --set-metadata gc.failure_class=hard --set-metadata "gc.failure_reason=<check names and URLs>"`,
then `gc bd close "<claimed-step-id>"`. `gc.failure_class=hard` ends the step
at once and review does not start; a failed outcome without it is handed back
as a new attempt, up to three times.

When push or open_pr is not `true`, do not open a pull request; the gate then
records `skipped: no publishing intent` and passes. If the branch has an open
pull request anyway, the gate still requires its checks to be green.

The handoff gate is `../assets/scripts/checks/implementation-handoff-valid.sh`:
the artifact validator described below, then
`../assets/scripts/checks/pr-ci-green.sh`. The controller does not wait for
CI. A step closed while a check is unfinished or red, or while the pull
request is marked ready instead of draft (`gh pr ready --undo <number>` makes
it a draft again), fails the gate and comes back to you as a new attempt with
the failing checks in `gc.attempt_log`; after three attempts the step fails and
review does not start. GitHub or the bead store being unreachable is not an attempt: the
gate retries, and says `INFRA` rather than `FAIL`.

Write or update the item summary with these schema-required body sections,
using the exact `##` headings below in this order:

- `## Summary`
- `## Intended Behavior`
- `## Changed Files`
- `## Verification`
- `## Remaining Risks`

The `## Verification` section must include both the first verification command
and the final proof command, with the observed pass/fail result.

The final proof command MUST be `make preflight` (or `make preflight-fast` when
the rig defines that target instead), run from the worktree root after all
other verification. This is the rig's full local-CI-equivalent gate; running
it here — before push, before CI — is what lets CI trust local verification
instead of re-running everything from a red start. Record the exact exit
code. Report the result honestly whether it passes or fails. When the workflow
does not publish (push or open_pr is not `true`), this step's own close
condition depends only on the artifact-schema validator below, not on whether
preflight itself passed — do not retry or attempt code fixes here on a
preflight failure alone, and do not withhold `gc.outcome=pass` because of
one: a failing preflight is then downstream review's finding to raise and
repair-review's loop to fix, not a second retry mechanism nested inside this
step. When the workflow does publish, CI runs the same gates on your pull
request and the handoff gate above holds this step until they are green, so
a preflight failure is yours to fix here.

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
  its exit code recorded. The run is required; a failing preflight result is
  still handled as described above.
- Every must-address checklist item on the work item is done.

Write the summary as a `gc.build.implementation-summary.v1` artifact at a
bead-scoped path outside the repo's tracked tree:
`{{artifact_root}}/task-<source-anchor-id>-summary.md`, the path
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
- `producer: {formula: do-work-item, stage: implement-item, attempt: <positive integer>}`
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

Artifact validation: this step is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the summary recorded at `gc.implementation.summary_path` (fallbacks `gc.build.implementation_summary_path`, then `gc.var.summary_path`) against schema `gc.build.implementation-summary.v1`. Before closing this step, read the launcher rig root from the workflow root bead's `gc.work_dir`, then run the pinned pack's copy of the same validator locally from that rig root (the command resolves the gate script through gc's formula layers, exactly as the controller does) with `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`; fix every reported validation error before setting `gc.outcome=pass`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the summary in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the summary.
