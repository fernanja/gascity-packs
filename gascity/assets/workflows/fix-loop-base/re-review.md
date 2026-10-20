This is the `fix-loop-base` methodology contract re-review step.

Concrete methodology packs override this step to call `{{code_review_formula}}`
after fixes. Continue only while the iteration count is below
`{{max_iterations}}`. Record the follow-up review report path on workflow root
metadata as `gc.build.review_report_path` before closing.

Review the local hand-off commit (reviewer-first, gc-gef2p8). CI does not run
between a fix pass and this re-review: the fix pass handed you a commit, not a
pull request, and CI runs once, on the commit that is finally approved, in the
publish step. The subject is `gc.build.handoff_commit` on this loop's workflow
root (with `gc.build.handoff_branch`), which the apply-fixes gate has verified
is on the work branch. If the key is empty, say so in the report and review the
head of the work branch at the launcher rig root instead.

Do not consult the pull request list or `gh pr checks`: there is no pull
request yet, and its check list is not evidence at this stage. Your test
evidence is the fix pass's local pass in its repair summary plus the runs you
make yourself below. If your checkout is on a different commit from the
subject, stop and say so in the report rather than reviewing one commit while
another is handed over.

Where to work (gc-mpaqx): in your own detached worktree of the commit under
review, never in the implementation worktree. That worktree belongs to a
closed bead and the engine can remove it between two of your commands, so
never depend on it existing. The commit is already in the rig repository,
which every worktree shares, so no fetch is needed. Make yours of the hand-off
commit at
`$GC_CITY/.gc/worktrees/$GC_RIG-scratch/re-review-<claimed-step-id>`, run
everything there, re-resolve the head there, and remove it before closing this
step. Name the commit you reviewed (`git rev-parse HEAD`
there) in the report. The exact commands, and what to do when a fresh worktree
lacks installed dependencies, are under "Where to work" in this pack's
`assets/workflows/build-from-review-base/review.md`.

Probe the fixed behaviour in a browser from that worktree against the available
seeded stack, then verify at the breadth set by `review_verification_scope` =
`{{review_verification_scope}}` (Jon, 2026-10-19, gc-65qlw7). `scoped` (default
pre-GA): run once the rig's full NON-browser test suites (ascent: `make
preflight-fast` plus `make test`) and the browser specs only for the files the
fix changed, on each configured device profile (ascent: `make preflight-fast`
stage 18); never the full browser suite, the full device matrix or the full
real-server collection locally, only the real-server specs for the areas the fix
touches. `full` (post-GA): add the complete browser suite on every configured
device profile and the rig's real-server suites, once each. CI's PR gate and the
weekly full-matrix run cover the rest. The repo's one-run rule holds: a second
run only after a failed first one, and only for the failure.

**Live-suite failures that are not findings (gc-aecuvz).** A full-suite run
meets a live host, so classify each failing test before raising it, and record
every command and exit code you use. Three classes:

1. Touched module: the failing spec, or anything it depends on, is in
   `git diff --name-only $(git merge-base origin/<base-branch> HEAD) HEAD`.
   "Anything it depends on" is explicit: the spec file, its fixtures, helpers
   and page objects, global setup and teardown, test and framework config,
   generated clients, and every module the failing spec imports, directly or
   transitively. If the branch changed any of them, the module is TOUCHED.
   `<base-branch>` is the PR's `baseRefName` when a PR exists, else the rig's
   default branch (`git symbolic-ref --short refs/remotes/origin/HEAD`, minus
   the `origin/` prefix). On a stacked branch, merge-base against that base
   branch, not against the parent branch. A touched module blocks, as before:
   a required finding. The flake-log rule for a touched module is unchanged
   (a one-off failure that does not reproduce on an immediate retry is
   logged; one that reproduces is fixed in the build).
2. Environment failure: ALL of these hold. The module is not touched by the
   branch. The collector recorded the failing request's URL or origin, and it
   is a THIRD-PARTY origin: not `localhost`, not `127.0.0.1`, not the rig's
   dev, API or e2e base-URL ports. The error is a Chromium network-stack code
   (`net::ERR_INTERNET_DISCONNECTED`, `ERR_NAME_NOT_RESOLVED`,
   `ERR_CONNECTION_*`, `ERR_NETWORK_CHANGED`) or a timeout fetching that
   third-party origin. When no URL or origin was recorded, the failure is NOT
   class 2: it is class 1 or class 3. After you confirm the host resolves
   `cdnjs.cloudflare.com` (`python3 -c 'import socket; print(socket.gethostbyname("cdnjs.cloudflare.com"))'`),
   one immediate retry of that test passes. List it under a
   `Permitted/environment` heading in the Verification section (test id,
   error code, failing origin, retry command, retry exit code). It is not a
   finding and does not change the verdict. If the retry fails too, it is
   class 3 or class 1.
3. Pre-existing: a reproducible failure in an untouched module that is not
   class 2. Run the same test on a detached worktree of `origin/<base-branch>`
   (`make review-worktree SHA=<base sha> DIR=<path>`), with the application
   under test built and served FROM that base worktree (`make dev` run in
   that worktree, on its own ports, with the test pointed at them through
   `E2E_BASE_URL` and `E2E_DJANGO_URL`), never the branch's dev server that
   may still be running. Pre-existing means the SAME test fails on the base
   with the SAME assertion and error text; a different failure on the base is
   not a match. Record both error texts side by side. When it matches, file
   one bead for it (`gc bd create`, with the test id, both error texts, and
   both commands and exit codes) and list it under a `Pre-existing` heading
   in the Verification section with the bead id. It is not a finding against
   this branch. When it passes on the base branch, or fails there differently,
   the branch causes it: a required finding.

Never use classes 2 and 3 for a touched module, for a first failure you did
not retry, or for a timeout or connection error against the rig's own server.

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

Artifact validation: this step is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the report recorded at `gc.build.review_report_path` against schema `gc.build.review.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the report in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the report. The schema id of this artifact is `gc.build.review.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.review.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).
