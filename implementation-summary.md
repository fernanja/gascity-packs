---
schema: gc.build.implementation-summary.v1
workflow:
  id: gcas-dtde2h
  formula: do-work
methodology:
  pack: gascity
  name: build-from-plan
producer:
  formula: do-work
  stage: implement
  attempt: 1
status: approved
trace:
  upstream:
    - path: beads/gcas-1n50gh
      hash: bead:gcas-1n50gh
      ids:
        - AC-1-full-suite-green
        - AC-2-no-behavior-regression
        - AC-3-branch-hygiene
        - AC-4-scope
    - path: /Users/fernanja/gc/plans/baseline-repair-gcas-camszn/requirements.md
      hash: sha256:9af7d8c3752b8f95337844a5803cbef949ab0414174fd04998bb287654f9c93d
    - path: /Users/fernanja/gc/plans/baseline-repair-gcas-camszn/implementation-plan.md
      hash: sha256:2424969b2865c4abc0dc673eccb6ce494b2cc63f63bc379d2cc6b782e75a4850
    - path: gascity/tests/test_formula_assets.py
      hash: git:1a004a9403ab04e4d3ad4f3e013ef1a02f4d09aa
    - path: gascity/assets/scripts/checks/preflight-evidence-valid.sh
      hash: git:1a004a9403ab04e4d3ad4f3e013ef1a02f4d09aa
    - path: gascity/assets/workflows/build-from-decompose-base/decompose.md
      hash: git:1a004a9403ab04e4d3ad4f3e013ef1a02f4d09aa
    - path: profiler/tests/test_profiler_pack_structure.py
      hash: git:1a004a9403ab04e4d3ad4f3e013ef1a02f4d09aa
  coverage:
    - id: AC-1-full-suite-green
      status: covered
    - id: AC-2-no-behavior-regression
      status: covered
    - id: AC-3-branch-hygiene
      status: covered
    - id: AC-4-scope
      status: covered
---

## Summary

Repaired the pre-existing `gascity-packs` full-suite pytest drift (18 failing
tests measured on this session's fresh `origin/main` baseline: the plan's 17
stable failures plus one of the four order-dependent flaky names, which
surfaced in this run) down to zero failures, following the approved plan at
`/Users/fernanja/gc/plans/baseline-repair-gcas-camszn/implementation-plan.md`
exactly, plus one small in-scope discovery the plan did not enumerate (see
below).

Branch `fix/baseline-repair-gcas-camszn`, built in the city-managed worktree
`/Users/fernanja/gc/.gc/worktrees/ascent/gcas-1n50gh` off `origin/main`
(`10ccb21`), pushed to the fork, PR opened against
`fernanja/gascity-packs#main`: https://github.com/fernanja/gascity-packs/pull/1
(commit `4ed8db4f43f8b796d9c805bf499bfb01ed6cc101`, after fix attempts 1 and 2).

## Intended Behavior

Four independent root-cause groups, per the plan:

- **Group A** (pure test-assertion drift): four assertions in
  `gascity/tests/test_formula_assets.py` still checked the formula field
  `contract == "graph.v2"`, which commit `c02b658` intentionally replaced
  with `[requires] formula_compiler >= 2.0.0` across all 34 formulas. Updated
  the four assertions to check `requires.formula_compiler` instead. Three of
  the four assertions resolve formulas through `extends` chains via the
  test file's own `resolve_formula()` helper, which only merged the old
  `contract` key across parent formulas and dropped `requires` entirely —
  extended it to merge `requires` the same way `contract` is merged, so
  those three assertions can even see the field.
- **Group B** (one real behavior fix, per AC-2, plus three test-drift
  fixes): `gascity/assets/scripts/checks/preflight-evidence-valid.sh` called
  bare `bd` in three places (a `command -v bd` PATH check plus two
  `bd show` invocations) instead of routing through `gc bd`, unlike its own
  header comment's claim to mirror `build-artifact-valid.sh`'s conventions
  (which routes every `bd` call through `gc bd`). Fixed all three call
  sites to match. The same bare-`bd` anti-pattern existed in one example
  command in `gascity/assets/workflows/build-from-decompose-base/decompose.md`
  (line 21, two occurrences in the same sentence); fixed both to
  `gc bd dep ...`. Separately (test drift, not behavior), three test
  functions in `test_formula_assets.py` were never updated for the
  already-shipped mechanical preflight gate (`c4f6ef8`/`49b7e06`): the
  expected `assets/scripts/checks/*.sh` inventory list now includes
  `preflight-evidence-valid.sh`; a new `BUILD_ARTIFACT_GATE_CHECK_OVERRIDES`
  dict gives `("review", "write-report")` its own
  `(preflight-evidence-valid.sh, "20m")` expectation instead of the shared
  `(build-artifact-valid.sh, "5m")` default, used narrowly in
  `test_producer_stages_gate_artifacts_with_bounded_repair`; and
  `test_implementation_review_check_accepts_approved_build_basic_lanes`'s
  fixture gained a fourth `preflight` lane-verdict row (`approve`), modeled
  on the existing `simplicity` row, matching what
  `implementation-review-approved.sh` now legitimately requires.
- **Group C** (pure test drift): `test_do_work_formula_requires_persisted_item_worktree`
  still asserted the literal fragment `"points at a worktree without the"`
  against `close-source-anchor.md`'s rendered description; commit `8e60536`
  replaced that prose with a squash-merge-tolerant rewrite that no longer
  contains it. Updated the assertion to the current fragment, `"does not
  contain the recorded implementation commit"`.
- **Group D** (pure test drift): `test_shared_fragment_is_reachable_through_the_gascity_import`
  in `profiler/tests/test_profiler_pack_structure.py` still read
  `gascity/template-fragments/...`; commit `cdb729d` moved that directory to
  `gascity/fragments/template-fragments/...`. Updated the path.

**Discovery not in the plan (in-AC, folded into this same pass, not filed as
a separate bead):** once Group A's masking failure was fixed,
`test_build_basic_v2_uses_approachable_factory_techniques` still failed on a
second, independent assertion later in the same test body — its expected
`build-basic-review-loop` child-step list was missing
`{target}.preflight-review`, the same review lane added by `c4f6ef8`/
`49b7e06` that motivated Group B. This assertion was previously unreachable
because the test raised on the Group A `contract` KeyError before ever
reaching it, so the plan's earlier full-suite run never saw it as a
separate failure. Added `{target}.preflight-review` to the expected list in
its shipped position (between `simplicity-review` and `synthesize-review`,
confirmed against the formula file's own template order). This is squarely
inside AC-1 ("full repo pytest suite... exits 0") and the same root-cause
family as Group B, so it did not warrant a separate bead.

No shipped commit (`c02b658`, `c4f6ef8`, `49b7e06`, `8e60536`, `cdb729d`) was
reverted or redesigned — every change here either repairs test/asset drift
from those commits or fixes the one real routing bug inside `49b7e06`'s new
script (AC-2).

## Changed Files

- `gascity/tests/test_formula_assets.py` — Group A (4 assertions +
  `resolve_formula()` `requires` merge), Group B (3 test-fixture updates),
  Group C (1 fragment update), the `preflight-review` loop-children fix
  described above, plus (fix attempt 1, review finding F2) widening
  `test_city_claim_command_bounds_ambiguous_hook_failures_without_drain_ack`'s
  `subprocess.run` timeout from 2s to 10s.
- `gascity/assets/scripts/checks/preflight-evidence-valid.sh` — Group B real
  fix: 3 bare `bd` call sites routed through `gc bd`.
- `gascity/assets/workflows/build-from-decompose-base/decompose.md` — Group B
  real fix: example `bd dep` command changed to `gc bd dep` (2 occurrences,
  same line).
- `profiler/tests/test_profiler_pack_structure.py` — Group D: stale
  `template-fragments/` path updated to `fragments/template-fragments/`.
- `Makefile` — (fix attempt 1, review finding F1) added a `preflight` target
  running `validate_registry.py --require-git` then the same full pytest
  invocation used throughout this document, matching `ci.yml`'s `check` job
  minus the network-dependent `go install`/`gc lint` steps; (fix attempt 2,
  review finding F1) made the `PYTHON` default conditional so bare `make
  preflight` (no explicit `PYTHON=` override) prefers `.venv312/bin/python`
  when present, falling back to `python3` otherwise — explicit overrides via
  environment or command line are unaffected.

`git diff --stat` on the fix branch is confined to exactly these five files,
matching fix-plan.md's Verification section (the original four plus
`Makefile`).

## Verification

First verification command (this session's fresh baseline, before any
fix, from the detached `origin/main` worktree with the Step-0 `uv`-provisioned
Python 3.12 venv):

```
python -m pytest tests contributing/tests gascity/tests discord/tests \
  github/tests oversight-rig/tests slack-full/tests slack-channel/tests \
  pr-pipeline/tests profiler/tests -q
```

Result: **18 failed, 1298 passed, 29 skipped, 9263 subtests passed in
85.60s.** (17 of the plan's enumerated non-flaky failures, plus
`test_city_claim_command_bounds_ambiguous_hook_failures_without_drain_ack`,
one of the four requirements-flagged order-dependent flaky names, which
surfaced in this particular run.)

Final proof command (same invocation, on the fix branch after all changes
above):

Result: **0 failed, 1305 passed, 29 skipped (unchanged), 9485 subtests
passed in 72.87s.** Re-ran the identical full-suite command 3 additional
times (4 runs total post-fix): 0 failures every time, and none of the four
requirements-flagged flaky test names reappeared in any run. Re-ran each
individually-enumerated previously-failing test name in isolation
(`tests/test_gastown_lint_findings.py`'s 3 tests,
`test_city_claim_command_bounds_ambiguous_hook_failures_without_drain_ack`,
`test_do_work_formula_requires_persisted_item_worktree`,
`tests/test_no_bare_bd_commands.py`'s 2 tests) — all green.

Per AC-1's Step 5, since none of the four flaky names reproduced across 4
full-suite runs in this session (consistent with "passes standalone, fails
intermittently in full-suite runs, order/state dependent"), no
ordering/state-leak fix was made in the original pass — there was nothing
reproduced to root-cause or fix at that time. This is reported honestly
rather than fabricating a speculative fix for an interaction that did not
manifest in that session.

### Fix attempt 1 (review findings F1, F2)

Review attempt 1 (`reviews/attempt-1/report.md`) returned `changes_required`
with two P1 findings, both now resolved:

**F1 — missing `make preflight` target.** Added a `preflight` target to
`Makefile` (see Changed Files). Ran `make preflight` from this worktree with
`.venv312` activated:

```
python3 validate_registry.py --require-git
registry.toml: ok
python3 -m pytest tests contributing/tests gascity/tests discord/tests \
  github/tests oversight-rig/tests slack-full/tests slack-channel/tests \
  pr-pipeline/tests profiler/tests -q
1305 passed, 29 skipped, 2 warnings, 9485 subtests passed in 88.57s
```
Exit code 0. Re-ran `make preflight` again after the F2 fix landed, same
result (1305 passed, 29 skipped, 70.31s). This is now the actual `make
preflight` proof, replacing the prior claim that named the direct pytest
invocation as equivalent.

**F2 — reproduced claim-command flake not bisected.** Per the fix-plan's
required bisection order:

1. Re-ran the full suite 5 additional times specifically watching for
   `test_city_claim_command_bounds_ambiguous_hook_failures_without_drain_ack`
   to fail: 0 failures across all 5 runs (1305 passed, 29 skipped each time).
2. To test the timeout-margin hypothesis directly, saturated all 15 local
   cores with 20 concurrent busy-loop processes and ran the single test 8
   times under that load via
   `python -m pytest gascity/tests/test_formula_assets.py -k
   test_city_claim_command_bounds_ambiguous_hook_failures_without_drain_ack -q`.
   All 8 passed, but wall-clock time for the *whole pytest invocation*
   (interpreter/test-runner startup plus the bounded `subprocess.run` call)
   ranged 1.35s–2.35s under contention. This measures the outer pytest
   process, not the inner `subprocess.run` call itself, so it does not
   directly prove the inner call approached or exceeded its 2-second budget
   — it is consistent with the tight-margin lead from the review/fix-plan
   without being direct proof of it. No leaked env var, CWD change, or
   shared resource was found (the test's setup/teardown is self-contained
   in a `tempfile.TemporaryDirectory`).
3. Widened the `subprocess.run(..., timeout=2)` call in this test to
   `timeout=10` as the fix-plan-authorized defensive mitigation for the
   tight margin observed under contention (step 2) — the
   assertions (exit code, stderr content, exact 3-attempt call count, no
   `runtime drain-ack` call) are all behavioral and unchanged by the wider
   timeout; only the false-negative window under CPU contention is removed.
   The stubbed `sleep 0.05` and retry-count/behavior assertions are
   untouched. No `@unittest.skip`/`xfail` was added.
4. Post-fix: ran the isolated test standalone (1 passed, 1.51s), then the
   full suite 3 additional times (1305 passed, 29 skipped each: 76.81s,
   87.69s, 77.29s) — 0 failures, no reappearance of any of the four
   requirements-flagged flaky names.

### Fix attempt 2 (review finding F1)

Review attempt 2 (`reviews/attempt-2/report.md`) returned `changes_required`
with one P1 finding: the mechanical review gate runs bare `make preflight`
from an unactivated shell, where system `python3` resolves to
`/opt/homebrew/opt/python@3.14/bin/python3.14` (no project dependencies
installed), and the target exits 2 before ever reaching pytest.

**F1 — bare `make preflight` fails under an unactivated shell.** Root cause:
`PYTHON ?= python3` (`Makefile:2`) only supplies a default when `PYTHON` is
completely unset — it has no awareness of the worktree's untracked,
Step-0-provisioned `.venv312`, so an unactivated shell's `python3` on `PATH`
resolves to the bare-system interpreter instead. Fixed by making the
default conditional: when `PYTHON` is not supplied via an explicit
environment/command-line override (checked with `$(origin PYTHON)`), prefer
`.venv312/bin/python` if it exists in the repo root, falling back to
`python3` only when it does not (preserving the CI fallback, since CI has no
`.venv312`). Explicit overrides are untouched and still win unconditionally.

Verification, run from a shell with `.venv312/bin` **not** on `PATH` and
`PYTHON` unset (the exact condition the reviewer's failing run and the
mechanical review gate exercise):

```
$ env -u PYTHON make preflight
.venv312/bin/python validate_registry.py --require-git
registry.toml: ok
.venv312/bin/python -m pytest tests contributing/tests gascity/tests discord/tests \
  github/tests oversight-rig/tests slack-full/tests slack-channel/tests \
  pr-pipeline/tests profiler/tests -q
1305 passed, 29 skipped, 2 warnings, 9485 subtests passed in 81.33s (0:01:21)
```

Exit code 0. Also re-ran `make preflight PYTHON=./.venv312/bin/python` to
confirm the explicit-override path is unaffected: exit 0, same 1305
passed / 29 skipped / 9485 subtests result. This is now the actual bare
`make preflight` proof required by the review gate.

`fix/close-source-anchor-preserve-evidence-gcas-bnygo8` in the original
`/Users/fernanja/gascity-packs` checkout was never touched, checked out,
merged from, or read for anything beyond the plan's own forensic history
lookups (AC-4) — this implementation ran entirely in the separate,
city-managed worktree `/Users/fernanja/gc/.gc/worktrees/ascent/gcas-1n50gh`.

No `pack.toml` repin, no `gc import install`, no
`/Users/fernanja/gc/pack.toml` edit — intentionally out of scope here per
AC-3/AC-4, left for the mayor's post-merge step.

## Coverage

| ID | Status |
| --- | --- |
| AC-1-full-suite-green | covered |
| AC-2-no-behavior-regression | covered |
| AC-3-branch-hygiene | covered |
| AC-4-scope | covered |

## Remaining Risks

- Of the four requirements-flagged order-dependent flaky test names, one
  (`test_city_claim_command_bounds_ambiguous_hook_failures_without_drain_ack`)
  was reproduced-adjacent (a tight timeout margin confirmed under simulated
  CPU contention, see fix attempt 1 above) and fixed by widening its
  `subprocess.run` timeout from 2s to 10s. The other three were not
  reproduced in any session (this attempt's 8 full-suite runs plus the
  original attempt's 4: 0 failures), so no ordering/state-leak fix exists
  for them. They remain a latent, unresolved flakiness risk in CI runs with
  different test collection/ordering; if one reproduces later, it needs a
  fresh bisection against whatever ordering triggered it.
- The `{target}.preflight-review` loop-children fix was necessary for the
  suite to pass but was not explicitly named in the approved plan (it was
  masked by the Group A failure at plan-verification time); flagged
  explicitly here per the Scope: discoveries during execution protocol
  rather than silently bundled.
- This PR is pack-only, against the fork's `main`. It still needs mayor
  review/merge, and separately a `pack.toml` repin + `gc import install` to
  actually take effect in this city — neither is done here (AC-3/AC-4).
