This is the `build-base` review stage. Treat it as a virtual contract that concrete formulas may override.

Review the implementation against the requirements, plan, decomposition, and
test evidence. Treat gap-analysis as one review lens inside this
post-implementation loop, not as a separate lifecycle stage. Findings must be
actionable and tied to concrete files, commands, or artifact paths.

As part of this review, actually run the rig's full local-CI-equivalent gate
yourself in your own review worktree (not the launcher checkout) — `make
preflight-fast` if the worktree's Makefile defines that target, otherwise
`make preflight` — and record the exact command and its outcome. Do not
accept or forward a prose claim about preflight from the implementation stage
as a substitute for running it here: that was tried and verified live to be
attention-dependent, not guaranteed (one re-review caught a missing run, an
identical re-review of a different item did not). A failing run is a required
fix, not missing evidence — findings must state what failed and where, not
that proof is absent.

Review the local hand-off commit (reviewer-first, gc-gef2p8): the subject is
`gc.build.handoff_commit` on the workflow root, checked out in a detached
scratch worktree of the rig repository (shared object store, no fetch). There is no pull request before the publish step, so its check list is
not evidence. Probe the change in a browser from that worktree, then run once:
the full browser suite on every configured device profile and the rig's
real-server suites against the available seeded stack. Test evidence is these
runs plus the worker's preflight record. Concrete formulas carry the full
procedure; see `build-from-review-base/review.md`.

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

Where to work (gc-mpaqx): in your own detached worktree of the commit under
review, never in the implementation worktree. That worktree belongs to a
closed bead and the engine can remove it between two of your commands, so
never depend on it existing. Make yours at
`$GC_CITY/.gc/worktrees/$GC_RIG-scratch/review-<claimed-step-id>`, run
everything there, re-resolve the head there, and remove it before closing
this step. The exact commands, and what to do when a fresh worktree lacks
installed dependencies, are under "Where to work" in this pack's
`assets/workflows/build-from-review-base/review.md`.

The requested review authority is `review_mode` {{review_mode}}. In `report`
mode, write findings and verdicts without mutating code. In `agent` mode, also
produce a structured fix handoff (findings plus fix guidance) that the caller's
review-fix formula applies; do not apply fixes from this stage. In
`interactive` mode, safe fixes may be negotiated or applied, and every change
and its reason must be recorded in the review artifact.

Write the review report to the resolved review report path and record that path
on the workflow root bead as `gc.build.review_report_path` before closing. Use
`gc bd update "<workflow-root-id>" --set-metadata "gc.build.review_report_path=<absolute path>"`.
Do not use `gc bd update --metadata 'key=value'`; `--metadata` only accepts a JSON
object.
Before closing this step, set the claimed step outcome with
`gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"`, then close
with `gc bd close "<claimed-step-id>" --reason "<concise reason>"`. Do not pass
`--metadata` or `--set-metadata` to `gc bd close`.

Close this step only when the implementation is clean enough to finalize or when unresolved findings are recorded.

Artifact validation: this stage is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the artifact recorded at `gc.build.review_report_path` against schema `gc.build.review.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the artifact in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the artifact. The schema id of this artifact is `gc.build.review.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.review.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).
