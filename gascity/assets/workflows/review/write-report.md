
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
record plus your own runs of the full browser suite on every configured device
profile and the rig's real-server suites, each run once (a second full run only
after a failed first). CI runs once, on the approved commit, in the publish
step.

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
