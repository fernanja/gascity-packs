This is the `build-from-review-base` review stage.

Review the implementation against:

- requirements_path: {{requirements_path}}
- plan_path: {{plan_path}}
- plan_review_path: {{plan_review_path}}
- decomposition_path: {{decomposition_path}}
- implementation_summary_path: {{implementation_summary_path}}
- code_review_formula: {{code_review_formula}}
- review_fix_formula: {{review_fix_formula}}
- implementation_formula: {{implementation_formula}}
- implementation_item_formula: {{implementation_item_formula}}
- review_mode: {{review_mode}}
- interaction_mode: {{interaction_mode}}
- max_iterations: {{max_iterations}}

Use the selected code review methodology to produce a review verdict and
findings. This stage records the review result; the following `repair-review`
stage owns any selected review-fix loop, restart handoff, or blocked repair
state.

Review the local hand-off commit (reviewer-first, gc-gef2p8). CI does not run
before this review: the implementation step hands you a commit, not a pull
request, and CI runs once, on the commit you approve, in the publish step.
Resolve the subject in this order and name the source in the report:

1. `gc.build.handoff_commit` (with `gc.build.handoff_branch`) on this
   workflow's root. For a build with implementation items, the
   `summarize-implementation` step writes these there (the last item's pair,
   with the ordered item root ids in `gc.build.handoff_items`); for a fix
   loop's re-review, the fix pass writes them on the loop's root;
2. when the root carries none (the summarize step did not run or recorded
   nothing), the hand-off recorded by the implementation item(s) the
   implementation summary names (the same key on each item's workflow root,
   or `gc.work_commit` on its source anchor), taking the last item's;
3. when no hand-off was recorded at all, the head of the work branch at the
   launcher rig root (`git -C <rig-root> rev-parse <branch>`), and say in the
   report that the hand-off was not recorded.

Do not consult the pull request list or `gh pr checks`: there is no pull
request yet, and its check list is not evidence at this stage. Your test
evidence is the worker's preflight record in the implementation summary plus
the runs you make yourself below. If a pull request for the branch already
exists anyway, do not rely on it; review the hand-off commit all the same. If
your checkout is on a different commit from the subject, stop and say so in
the report rather than reviewing one commit while another is handed over.

Where to work (gc-mpaqx). Run every command of this review in your own
worktree, never in the implementation worktree and never in the launcher
checkout. The implementation worktree belongs to a work-item bead that is
already closed, and the engine removes a closed bead's worktree under
`$GC_CITY/.gc/worktrees/$GC_RIG/` once it is clean and no process is running
in it, which is its state between two of your commands (two reviews lost their
checkout mid-run). Never depend on it existing. The hand-off commit is already
in the rig repository, which every worktree shares, so no fetch is needed. From
the launcher rig root, make a detached worktree of that commit in the scratch
directory next to it, which that cleanup never looks at:

```bash
WT="$GC_CITY/.gc/worktrees/$GC_RIG-scratch/review-<claimed-step-id>"
git worktree add --detach "$WT" <handoff-commit>
git -C "$WT" rev-parse HEAD
```

The last command must print the hand-off commit. If `$WT` is left over from an
earlier attempt, reuse it. When the subject moves (a new hand-off after a fix
pass), bring the worktree to it (`git -C "$WT" checkout --detach
<new-handoff-commit>`) and resolve the head there again. A fresh worktree has
no installed dependencies: when a tool or module
is missing, install it the way the repo documents (its own install target, for
example `make install`) and run again. A tool missing from a fresh worktree is
not a defect in the change and is not a finding. Before closing this step,
after the verdict and the reviewed commit are recorded, remove the worktree
from the launcher rig root with `git worktree remove --force "$WT"`; nothing
else cleans that directory.

Probe behaviour from that worktree: start the application from it against the
available seeded stack and exercise the changed behaviour in a browser, not
only in tests. Then run, once each: the full browser suite on every configured
device profile (phone and tablet profiles included), and the rig's real-server
suites against the available seeded stack. These are the suites CI does not
run or the worker did not, and the defects they find are the reason this
review comes before CI. The repo's one-full-run rule holds: a second full run
only after a failed first one, and only for the failure.

**Live-suite failures that are not findings (gc-aecuvz).** A full-suite run
meets a live host, so classify each failing test before raising it, and record
every command and exit code you use. Three classes:

1. Touched module: the failing spec, or the code it exercises, is in
   `git diff --name-only $(git merge-base origin/<base-branch> HEAD) HEAD`.
   It blocks, as before: a required finding. The flake-log rule for a touched
   module is unchanged (a one-off failure that does not reproduce on an
   immediate retry is logged; one that reproduces is fixed in the build).
2. Environment failure: ALL of these hold. The module is not touched by the
   branch. The error is a Chromium network-stack code
   (`net::ERR_INTERNET_DISCONNECTED`, `ERR_NAME_NOT_RESOLVED`,
   `ERR_CONNECTION_*`, `ERR_NETWORK_CHANGED`) or a timeout fetching a
   third-party origin. After you confirm the host resolves
   `cdnjs.cloudflare.com` (`python3 -c 'import socket; print(socket.gethostbyname("cdnjs.cloudflare.com"))'`),
   one immediate retry of that test passes. List it under a
   `Permitted/environment` heading in the Verification section (test id,
   error code, retry command, retry exit code). It is not a finding and does
   not change the verdict. If the retry fails too, it is class 3 or class 1.
3. Pre-existing: a reproducible failure in an untouched module that is not
   class 2. Run the same test on a detached worktree of `origin/<base-branch>`.
   When it fails there too, file one bead for it (`gc bd create`, with the test
   id, the error, and both commands and exit codes) and list it under a
   `Pre-existing` heading in the Verification section with the bead id. It is
   not a finding against this branch. When it passes on the base branch, the
   branch causes it: a required finding.

Never use classes 2 and 3 for a touched module, for a first failure you did
not retry, or for a timeout against the rig's own server.

A requirement the implementation summary marks deferred, not run, skipped or
"left for a later stage" is a required finding unless the requirements
artifact permits that deferral in so many words; name the requirement and
quote the summary (gc-gdyaz).

As part of this review, actually run the rig's full local-CI-equivalent gate
yourself in your own review worktree (not the launcher checkout) — `make
preflight-fast` if the worktree's Makefile defines that target, otherwise
`make preflight` — and record the exact command and its outcome. Do not
accept or forward a prose claim about preflight from the implementation stage
as a substitute for running it here: that was tried and verified live to be
attention-dependent, not guaranteed (one re-review caught a missing run, an
identical re-review of a different item did not). A failing run is a required
fix (`changes_required`), not missing evidence.

The preflight gate is not a test run for every kind of change. Find out what it
actually executes in this repo before treating it as coverage (in the ascent
repo, `make preflight-fast` runs no Django tests at all). For every test module
the diff adds or changes, and for the tests that cover each source file the
diff changes, run them yourself in your own review worktree with the repo's
own test command (for example `make test ARGS='<module> <module>'`) and record
the exact command, the tally and the exit code in the report. A test the diff
itself adds or edits that you have not seen pass is missing evidence: the
verdict cannot be `approved`. After a merge of the default branch into the
work branch, run them again on the merged commit: a test can pass on the
branch and fail once the default branch's changes arrive (gc-4kgy1: a review
approved, and publish pushed, a commit whose own new test had been failing
since the merge).

Verify load-bearing facts before any wording concern: the SHAs, what actually
failed, and the failing code's content at the failure SHA. Accept a fix for an
intermittent or timing failure only with evidence that the pre-fix code fails
and the post-fix code passes under the same reproduction method (throttling,
repeats, a stress harness; the repo's AGENTS.md names it). If the pre-fix code
cannot be made to fail, the verdict is `blocked` with reason `cannot_reproduce`
and the evidence gathered recorded: never `approved`, never a skip. Never
demand evidence the repo cannot produce.

Report the class of a defect, not only the instance. When you find one, look
for every other occurrence of the same kind in the diff and in the code it
touches, and list them all in the same finding (file and line for each), so
one fix pass can close the class. Say what would make the defect impossible,
not only what is wrong with this line. Naming one instance while its
neighbours are in view costs a full extra round for each of them (gc-f6est:
one test was sent back five times, one hole per review).

When a finding is a narrower version of one raised earlier in this artifact
root, say so, and state the acceptance test that settles the whole class: the
condition under which it will not be raised again.

For `review_mode=report`, write findings and verdicts without mutating code.
For `review_mode=agent`, write a structured fix handoff for the caller or
selected fix loop. For `review_mode=interactive`, safe fixes may be negotiated
or applied, but every change and reason must be recorded.

Close this step only when the implementation has a concrete review verdict:
`approved`, `changes_required`, or `blocked`. Record the review report path,
verdict, unresolved findings, drift observations, and any existing fix-attempt
count on the workflow root metadata.

When the verdict is `approved`, also record `gc.build.review_subject_commit`
on the workflow root, resolved fresh right now from the actual reviewed
worktree's `git rev-parse HEAD` — never copied from `implementation_summary_path`,
an earlier continuation's publish metadata, or any other prior run's value.
This is the only sha the publish step is allowed to treat as approved for
push/PR purposes (gc-ajt3i: a publish step trusted a stale pre-repair commit
here and opened a PR 3 approved commits behind).

## Closing this step

The result lives on the workflow root (`gc.root_bead_id` on your claimed step
bead), not on the step bead and not in a mail to the mayor; writing it there
is part of this step (gc-xhumy: a reviewer recorded the fields on its own
step bead and mailed the mayor to mirror them, and the build waited for the
copy). Before closing, run exactly these, in this order:

1. Record the result on the root, in one update:
   `gc bd update "<workflow-root-id>" --set-metadata "gc.build.review_report_path=<absolute report path>" --set-metadata "gc.build.review_verdict=<approved|changes_required|blocked>"`,
   with `--set-metadata "gc.build.review_subject_commit=<sha>"` added when
   the verdict is `approved`.
2. Check that it landed:
   `gc bd show "<workflow-root-id>" --json | jq -r '(if type == "array" then .[0] else . end).metadata | .["gc.build.review_report_path"], .["gc.build.review_verdict"]'`
   must print the path and the verdict you set, nothing else.
3. Run the gate yourself, `build-artifact-valid.sh` (the command is under
   Artifact validation below): it reads `gc.build.review_report_path` from
   the root and fails with `no artifact path recorded on workflow root` when
   step 1 was skipped.
4. Set `gc.outcome=pass` on your claimed step bead and close it.

Artifact validation: this stage is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.review_report_path` against schema `gc.build.review.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact. The schema id of this artifact is `gc.build.review.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.review.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).
