This is the `implementation-item-base` methodology contract shared-drain item
step.

Concrete methodology packs override this step to perform one single-lane item
of implementation work with optional context from `{{context_path}}`. Do not
create parallel work inside this shared-drain contract.

Default fallback behavior must still enforce the worktree contract: resolve the
source anchor from workflow metadata, read the authoritative worktree from the
source anchor, and `cd "$WORKTREE"` before source reads, edits, tests, hashes,
or commits. `gc.work_dir` is the launcher rig root, not the implementation
worktree. When reading beads with `gc bd show --json`, handle both an object and a
one-element list before reading metadata.

The launcher checkout's `main` is never a valid commit target, including under
pressure to unblock validation. If verification needs code that appears
unreachable from `main` (an earlier item's commit orphaned, a sibling
worktree's work not yet merged), that is a signal to re-resolve `$WORKTREE`/the
source anchor or fail the step with a clear diagnostic — not license to
cherry-pick, merge, or otherwise commit anything onto the shared rig root's
checked-out branch to make validation pass locally. The only path from a
worktree to `main` is a GitHub PR through the pack's normal publish step;
nothing before that step may write to `main` directly, for any reason.

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
   `perl -e '$t=shift; $p=fork; exec @ARGV unless $p; $SIG{ALRM}=sub{kill "TERM",$p; exit 124}; alarm $t; waitpid $p,0; exit $?>>8' 1500 gh pr checks <number> --watch --interval 30`.
   This `perl` wrapper is the time limit that works on macOS and Linux alike
   (GNU `timeout` is not installed on macOS, and a bare `alarm` before `exec`
   does not stop `gh`, which ignores that signal). The command returns by
   itself when the checks finish (0: green, 1: a check failed) and the wrapper
   ends it after 25 minutes (status 124), so it is not a blocking command of
   the kind the shell rules forbid. If your shell tool allows less than that for one
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
   the base branch also has is different: when the same check is red on the
   base branch's own most recent run of it, and the base branch does not
   require that check, the gate lets it pass and prints a `WARNING` line
   naming both runs. Do not fix the base branch's failure on this branch, and
   do not pass over it in silence: copy that `WARNING` line, with both run
   URLs, into the summary's `## Remaining Risks`. The gate makes this call,
   not you: a red check that the base branch requires, that is green on the
   base branch, or that the base branch has never run, blocks. If the failure
   is in CI's own machinery (a runner that died, a download that timed out),
   you may rerun the failed job once (`gh run rerun <run-id> --failed`) and
   must record in the summary that you did, with the run URL and both results.
   The gate does not tell a flake from a defect: a check that is still red is
   red.
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
it a draft again), fails the gate and comes back as a new attempt with the
failing checks in `gc.attempt_log`; after three attempts the step fails and
review does not start. GitHub or the bead store being unreachable is not an attempt: the
gate retries, and says `INFRA` rather than `FAIL`. A concrete methodology
pack that overrides this step keeps this handoff or replaces it with its own
equivalent gate.

Write the per-item implementation summary as a
`gc.build.implementation-summary.v1` artifact at a bead-scoped path outside
the repo's tracked tree:
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

The summary body must contain these exact schema-required `##` headings in this
order:

- `## Summary`
- `## Intended Behavior`
- `## Changed Files`
- `## Verification`
- `## Remaining Risks`

Every requirement id the requirements artifact defines (`AC-1`, `SCOPE-2`,
`REQ-3`, `OQ-4`, `CON-5` and the like, where it leads a list item, a heading or
a paragraph) needs a `trace.coverage` entry; the gate reads the ids from the
requirements file itself (a build split into several work items is the
exception: each item's summary covers what its own work item delivers). An
entry with any status other than `covered` needs a
`permit` beside its `rationale`: the sentence in the requirements artifact that
hands the requirement off, quoted word for word, at least 20 characters. The
quote must itself say the requirement is for later, for someone else, or not
for this work ("post-merge", "out of scope", "follow-up bead", "mayor-owned",
"deferred" and the like); the requirement's own statement is not a permit. The
gate checks the quote against the requirements artifact recorded on the
workflow root or, for an implementation item, on the workflow that launched it
(`gc.build.requirements_path`, fallback `gc.var.requirements_path`) and rejects
the summary when the permit is missing, its text is not there, or it hands
nothing off (gc-gdyaz). If no root records a requirements artifact, the gate
says so and names the command that records it; it never checks a quote against
the work item. A required check that has not been run is work still to do, not
a deferral: run it, or write the summary with `status: blocked` and say what is
missing.

Artifact validation: this step is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the summary recorded at `gc.implementation.summary_path` (fallbacks `gc.build.implementation_summary_path`, then `gc.var.summary_path`) against schema `gc.build.implementation-summary.v1`. Before closing this step, read the launcher rig root from the workflow root bead's `gc.work_dir`, then run the pinned pack's copy of the same validator locally from that rig root (the command resolves the gate script through gc's formula layers, exactly as the controller does) with `GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"`; fix every reported validation error before setting `gc.outcome=pass`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the summary in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the summary.
