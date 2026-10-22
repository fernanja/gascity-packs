
Write the review verdict report to {{report_path}} with pass/fail, findings,
missing evidence, and recommended fixes for subject {{subject_path}}.

For each implementation item under review, its implementation summary is
required to record a final proof command of `make preflight` (or
`make preflight-fast`) with the observed exit code — this is the rig's full
local-CI-equivalent gate, and a passing run is what lets CI trust local
verification instead of re-running everything. Check it explicitly:

- Missing entirely (no final proof command recorded, or it isn't
  `make preflight`/`make preflight-fast`): treat as missing evidence — the
  verdict cannot be `approved`.
- Recorded with a non-zero exit code: this is a required finding. The verdict
  must be `changes_required`, naming the failing item and quoting the
  recorded failure.
- Recorded with exit code 0: cite it as passing evidence for that item.

Reviewer-first (gc-gef2p8): the subject under review is the local hand-off
commit, `gc.build.handoff_commit` on the workflow root, checked out as
`git worktree add --detach <scratch> <sha>` from the rig repository. Name that
sha in the report. No pull request exists before the publish step, so the PR
check list is not required evidence; the evidence is the worker's preflight
record plus your own verification run, whose breadth is set by
`review_verification_scope` = `{{review_verification_scope}}` (owner decision,
Jon 2026-10-19, gc-65qlw7):

- `scoped` (the default until GA): run the rig's full NON-browser test suites
  once (ascent: `make preflight-fast`, which includes the full jsdom/unit
  suite, plus `make test` for the full Django suite) and the browser specs
  only for the files the hand-off changed, on each configured device profile
  (ascent: `make preflight-fast` stage 18 does exactly this). Do not run the
  full browser suite or the full device matrix locally: pre-GA, re-running
  low-risk redundant tests costs hours per review and host-wide load; the
  rig's weekly CI run of the complete browser matrix is the backstop
  (ascent vitest-browser.yml `schedule`, Sunday 06:17 UTC, reported under
  `vitest-browser-gate (full matrix)`), and the PR gate runs the browser
  suite on its PR profile (ascent: Desktop Chrome, 2 shards).
- `full` (post-GA hardening, flip the variable, no prompt edit): in addition,
  run the complete browser suite on every configured device profile and the
  rig's real-server suites once (a second run only after a failed first).

Either way, run extra device profiles locally only for the changed specs, and
only when the hand-off changes shared layout, design-system primitives or the
test harness itself; say so in the report.

**Live-suite failures that are not findings (gc-aecuvz).** A full-suite run
meets a live host, so classify each failing test before raising it, and record
every command and exit code you use. Run each selected suite to completion in
one pass, so that every failure surfaces in one round: never pass
`--max-failures`, `-x`, `--bail` or any equivalent stop-at-first-failure flag in
a review run (a run that stops at the first failure shows one unrelated failure
per fix loop: five loops on one build, fix-loop deep dive 2026-10-22). Three
classes:

1. Changed spec: the failing test's own spec file is in
   `git diff --name-only $(git merge-base origin/<base-branch> HEAD) HEAD`, or
   the build added it. `<base-branch>` is the PR's `baseRefName` when a PR
   exists, else the rig's default branch (`git symbolic-ref --short
   refs/remotes/origin/HEAD`, minus the `origin/` prefix). On a stacked branch,
   merge-base against that base branch, not against the parent branch. A
   changed spec blocks, as before: a required finding. The flake-log rule for a
   changed spec is unchanged (a one-off failure that does not reproduce on an
   immediate retry is logged; one that reproduces is fixed in the build).
2. Environment failure: ALL of these hold. The spec is not changed by the
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
3. Untouched spec: the failing test's spec file is NOT in that diff and the
   build did not add it, even when a fixture, helper, page object, config
   file, generated client or imported module the spec uses is, and the failure
   is not class 2. It is a required finding only when a differential check
   shows the branch causes it. Run the identical command 2 times on the head
   under review and 2 times on a detached worktree of `origin/<base-branch>`
   (`make review-worktree SHA=<base sha> DIR=<path>`), with the application
   under test built and served FROM that base worktree (`make dev` run in
   that worktree, on its own ports, with the test pointed at them through
   `E2E_BASE_URL` and `E2E_DJANGO_URL`), never the branch's dev server that
   may still be running. Branch-caused means it fails 2/2 on the branch AND
   passes 2/2 on the base: a required finding. Any other outcome (it fails on
   the base too, passes on the branch, or the runs disagree) is not a finding
   against this branch. One passing base run does not prove the branch is at
   fault. File one bead for it, or add the runs to the bead that already
   tracks that test (`gc bd create`, with the test id, both run sets, the
   commands and exit codes, and the error text of each failing run), and list
   it under a `Pre-existing or flaky` heading in the Verification section with
   the bead id.

Never use class 2 or class 3 for a changed spec, for a first failure you did
not retry, or for a timeout or connection error against the rig's own server.

This is the only place a preflight failure is gated — do not re-run preflight
yourself here, and do not invent a separate fix loop; a `changes_required`
verdict already routes through this workflow's existing repair-review stage.

The requested review authority is `{{review_mode}}`: in `report` mode, write
findings and verdicts without mutating code; in `agent` mode, also include a
structured fix handoff for the caller's review-fix formula to apply; in
`interactive` mode, safe fixes may be negotiated or applied with every change
and reason recorded in the report. The interaction posture is
`{{interaction_mode}}`.

Artifact validation: this step is gated by `../assets/scripts/checks/build-artifact-valid.sh`, which validates the report recorded at `gc.build.review_report_path` (fallback `gc.var.report_path`) against schema `gc.build.review.v1`. On repair attempts (`gc.attempt` greater than 1), read the validator errors from `gc.attempt_log` on the validation loop control bead (the dependent of this step bead) and repair the report in place instead of rewriting it. Two bounded repair attempts follow the first failure; exhausting them closes this stage with `gc.outcome=fail` and machine-readable validation errors that block downstream stages. Never ask questions in headless mode; record unresolved ambiguity inside the report. The schema id of this artifact is `gc.build.review.v1` (never the file name), and the validator's one entrypoint is its command line, run from the launcher rig root: `python3 "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/validate_build_artifact.py") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')" --schema gc.build.review.v1 --path <artifact file>`; it exits 0 with `{"ok": true}` or 1 with one `error:` line to fix. The file-to-schema-id table for every build artifact is `assets/workflows/artifact-validation.md` in the same pack layer as that script (gc-0dbwj9: operators guessed a `validate_artifact_text` entrypoint and a `plan-review` schema id; neither exists).

## Required artifact location

`{{report_path}}` is relative to the durable rig root, not the current
per-bead worktree. Read the rig root from `$GC_RIG_ROOT` and write the report
to `$GC_RIG_ROOT/{{report_path}}`; do not write it under the current directory
or `$GC_WORK_DIR`. The inference gate reads the artifact from that durable
location after this disposable worktree is removed.

Before closing, resolve the workflow-root id from the claimed bead's
`gc.root_bead_id`, then record the rig-relative path on that root:

```bash
gc bd update "<workflow-root-id>" \
  --set-metadata 'gc.build.review_report_path={{report_path}}'
```

From `$GC_RIG_ROOT`, run the pinned pack's copy of the artifact validator (the
command resolves the gate script through gc's formula layers, exactly as the
controller does) with the claimed bead id. Fix any error before setting
`gc.outcome=pass`:

```bash
GC_BEAD_ID=<claimed-step-id> "$(gc formula list --json | python3 -c 'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])')"
```
