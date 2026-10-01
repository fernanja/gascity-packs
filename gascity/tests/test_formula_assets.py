from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest


FORMULAS = {
    "build-base",
    "build-basic",
    "build-basic-review",
    "build-from-convoy",
    "build-from-convoy-base",
    "build-from-decompose",
    "build-from-decompose-base",
    "build-from-plan",
    "build-from-plan-base",
    "build-from-requirements",
    "build-from-requirements-base",
    "build-from-review",
    "build-from-review-base",
    "code-review-base",
    "decomposition-base",
    "design-review",
    "do-work",
    "do-work-item",
    "fix-convoy",
    "fix-loop-base",
    "gap-analysis",
    "github-issue-fix",
    "github-issue-fix-base",
    "github-issue-fix-design-review-work",
    "github-issue-triage-base",
    "github-issue-triage",
    "github-pr-review",
    "implement",
    "implementation-base",
    "implementation-item-base",
    "planning-base",
    "publish",
    "review",
    "same-session-implement",
}

ROLE_AGENTS = {
    "design-author",
    "design-implementation-reviewer",
    "design-test-risk-reviewer",
    "gap-analyst",
    "implementation-reviewer",
    "implementation-worker",
    "issue-triager",
    "publisher",
    "requirements-planner",
    "review-synthesizer",
    "run-operator",
    "task-decomposer",
}

CATALOG_FORMULAS = {
    "build-basic",
    "build-from-convoy",
    "build-from-decompose",
    "build-from-plan",
    "build-from-requirements",
    "build-from-review",
    "design-review",
    "gap-analysis",
    "github-issue-fix",
    "github-issue-triage",
    "github-pr-review",
    "implement",
    "review",
}

BUILD_BASE_STEPS = [
    "prepare",
    "requirements",
    "plan",
    "plan-review",
    "decompose",
    "implement",
    "implement-same-session",
    "summarize-implementation",
    "review",
    "finalize",
    "publish",
]

BUILD_FROM_REVIEW_STEPS = {
    "prepare-review",
    "review",
    "repair-review",
    "finalize",
    "publish",
}

BUILD_FROM_CONVOY_STEPS = BUILD_FROM_REVIEW_STEPS | {
    "prepare-convoy",
    "implement",
    "implement-same-session",
}

BUILD_FROM_DECOMPOSE_STEPS = BUILD_FROM_CONVOY_STEPS | {
    "prepare-decompose",
    "decompose",
}

BUILD_FROM_PLAN_STEPS = BUILD_FROM_DECOMPOSE_STEPS | {
    "prepare-plan",
    "plan",
    "plan-review",
}

BUILD_FROM_REQUIREMENTS_STEPS = BUILD_FROM_PLAN_STEPS | {
    "prepare-requirements",
    "requirements",
}

METHODOLOGY_STAGE_CONTRACTS = {
    "planning-base": {
        "steps": ["prepare-planning", "requirements", "plan", "plan-review"],
        "target_required": False,
        "vars": {
            "artifact_root",
            "context_path",
            "requirements_path",
            "plan_path",
            "interaction_mode",
            "review_mode",
        },
    },
    "decomposition-base": {
        "steps": ["decompose"],
        "target_required": False,
        "vars": {"context_path", "plan_path", "decomposition_path"},
    },
    "implementation-base": {
        "steps": ["prepare-worktree", "implement", "close-source-anchor"],
        "target_required": True,
        "vars": {"context_path", "implementation_target", "summary_path", "push", "open_pr"},
    },
    "implementation-item-base": {
        "steps": ["implement-item"],
        "target_required": True,
        "vars": {"context_path", "implementation_target", "push", "open_pr"},
    },
    "code-review-base": {
        "steps": ["validate-context", "write-report"],
        "target_required": False,
        "vars": {
            "context_path",
            "subject_path",
            "report_path",
            "interaction_mode",
            "review_mode",
        },
        "mode": "report",
    },
    "fix-loop-base": {
        "steps": ["plan-fixes", "apply-fixes", "re-review"],
        "target_required": False,
        "vars": {
            "context_path",
            "findings_path",
            "implementation_formula",
            "implementation_target",
            "code_review_formula",
            "max_iterations",
            "push",
            "open_pr",
        },
    },
}

METHODOLOGY_FORMULA_VARS = {
    "planning_formula": "planning-base",
    "decomposition_formula": "decomposition-base",
    "implementation_formula": "implement",
    "implementation_item_formula": "do-work-item",
    "code_review_formula": "review",
    "review_fix_formula": "fix-loop-base",
}

# Closed methodology metadata vocabulary; values outside these sets are
# contract violations (GC-METH-BR-034).
METHODOLOGY_METADATA_VOCABULARY = {
    "allowed_drain_policies": {"separate", "same-session"},
    "implementation_strategy": {"drain", "convoy-step"},
    "interaction_modes": {"interactive", "autonomous", "headless"},
    "review_modes": {"report", "agent", "interactive"},
}

# Top-level build formulas that must declare [metadata.gc.methodology]
# (GC-METH-BR-033), keyed by formula name -> pack dir relative to packs root.
TOP_LEVEL_BUILD_FORMULA_PACKS = {
    "build-base": "gascity",
    "build-basic": "gascity",
    "compound-build": "compound-engineering",
    "superpowers-build": "superpowers",
    "bmad-build": "bmad",
    "gstack-build": "gstack",
}

# Mode selector vars and their pinned defaults per formula
# (GC-METH-BR-019..024). github-issue-fix-base interaction_mode defaults empty
# because the snapshot step normalizes it from the backward-compatible `mode`
# alias into workflow root metadata gc.var.interaction_mode.
MODE_VAR_DEFAULTS = {
    "build-base": {"interaction_mode": "interactive", "review_mode": "agent"},
    "build-basic": {"interaction_mode": "interactive", "review_mode": "agent"},
    "planning-base": {"interaction_mode": "interactive", "review_mode": "report"},
    "code-review-base": {"interaction_mode": "autonomous", "review_mode": "report"},
    "review": {"interaction_mode": "autonomous", "review_mode": "report"},
    "github-issue-fix-base": {"interaction_mode": "", "review_mode": "agent"},
    "github-pr-review": {"interaction_mode": "interactive", "review_mode": "report"},
}

BUILD_ARTIFACT_CHECK_SCRIPT = "../assets/scripts/checks/build-artifact-valid.sh"

# Agents pre-run the gate from the rig root; this resolves the same pinned-pack
# script gc's parser picks (last formula layer that ships it), gc-yrouy.
MANUAL_BUILD_ARTIFACT_CHECK_COMMAND = (
    "GC_BEAD_ID=<claimed-step-id> "
    '"$(gc formula list --json | python3 -c \'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/build-artifact-valid.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])\')"'
)

# Per-gate overrides of (script, timeout) for gates whose formula wires a
# stricter check than the shared BUILD_ARTIFACT_CHECK_SCRIPT / "5m" default.
BUILD_ARTIFACT_GATE_CHECK_OVERRIDES = {
    ("review", "write-report"): ("../assets/scripts/checks/preflight-evidence-valid.sh", "20m"),
    # The steps that end implementation chain the artifact gate with the
    # CI-green handoff gate (gc-68exu).
    ("implementation-base", "implement"): ("../assets/scripts/checks/implementation-handoff-valid.sh", "5m"),
    ("do-work", "implement"): ("../assets/scripts/checks/implementation-handoff-valid.sh", "5m"),
    ("implementation-item-base", "implement-item"): ("../assets/scripts/checks/implementation-handoff-valid.sh", "5m"),
    ("do-work-item", "implement-item"): ("../assets/scripts/checks/implementation-handoff-valid.sh", "5m"),
}

# Steps that hand a commit to review close only on green GitHub checks
# (gc-68exu): formula -> step -> (check script, step doc).
CI_GREEN_HANDOFF_STEPS = {
    ("implementation-base", "implement"): ("implementation-handoff-valid.sh", "implementation-base/implement.md"),
    ("do-work", "implement"): ("implementation-handoff-valid.sh", "do-work/implement.md"),
    ("implementation-item-base", "implement-item"): (
        "implementation-handoff-valid.sh",
        "implementation-item-base/implement-item.md",
    ),
    ("do-work-item", "implement-item"): ("implementation-handoff-valid.sh", "do-work-item/implement-item.md"),
    ("fix-loop-base", "apply-fixes"): ("pr-ci-green.sh", "fix-loop-base/apply-fixes.md"),
}
# `gh` ignores SIGALRM, so the wrapper forks and terminates it (GNU timeout is
# not installed on macOS).
BOUNDED_CI_WAIT_COMMAND = (
    "`perl -e '$t=shift; $p=fork; exec @ARGV unless $p; $SIG{ALRM}=sub{kill \"TERM\",$p; exit 124}; "
    "alarm $t; waitpid $p,0; exit $?>>8' 1500 gh pr checks <number> --watch --interval 30`"
)
HARD_FAILURE_CLOSE_COMMAND = (
    '`gc bd update "<claimed-step-id>" --set-metadata gc.outcome=fail --set-metadata gc.failure_class=hard '
    '--set-metadata "gc.failure_reason='
)
MANUAL_PR_CI_GREEN_COMMAND = (
    "GC_BEAD_ID=<claimed-step-id> "
    '"$(gc formula list --json | python3 -c \'import json,os,sys; c=[os.path.join(os.path.dirname(p),"assets/scripts/checks/pr-ci-green.sh") for p in json.load(sys.stdin)["search_paths"]]; print([p for p in c if os.path.isfile(p)][-1])\')"'
)

# One produce attempt plus two bounded schema-repair attempts per artifact stage.
BUILD_ARTIFACT_GATE_MAX_ATTEMPTS = 3

# Producer stages whose artifact may leave a requirement uncovered only with a
# permit quoting the requirements artifact (gc-gdyaz). The switch travels as
# step metadata so the rule and the step text that explains it install together.
COVERAGE_PERMIT_STEPS = {
    ("build-from-plan-base", "plan"): "build-from-plan-base/plan.md",
    ("do-work", "implement"): "do-work/implement.md",
    ("do-work-item", "implement-item"): "do-work-item/implement-item.md",
    ("implementation-base", "implement"): "implementation-base/implement.md",
    ("implementation-item-base", "implement-item"): "implementation-item-base/implement-item.md",
}
COVERAGE_PERMIT_REQUIREMENTS = (
    "# Requirements\n\n"
    "- **AC-1:** the guard rejects a team-less item.\n"
    "- **AC-2:** after merge the alerts auto-close. The mayor checks this\n"
    "  post-merge; it is not a worker AC.\n"
)
COVERAGE_PERMIT_QUOTE = "The mayor checks this post-merge; it is not a worker AC."


def coverage_permit_plan(permit: str | None, *, status: str = "approved") -> str:
    permit_line = f"      permit: {json.dumps(permit)}\n" if permit is not None else ""
    sections = "\n\n".join(
        f"## {name}\n\n{name} content."
        for name in ("Current System", "Proposed Implementation", "Non-Goals", "Verification")
    )
    return (
        "---\n"
        "schema: gc.build.plan.v1\n"
        "workflow:\n  id: root\n  formula: build-from-plan\n"
        "methodology:\n  pack: gascity\n  name: build-from-plan\n"
        "producer:\n  formula: build-from-plan\n  stage: plan\n  attempt: 1\n"
        f"status: {status}\n"
        "trace:\n"
        "  upstream:\n"
        "    - path: requirements.md\n"
        "      hash: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "      ids: [AC-1, AC-2]\n"
        "  coverage:\n"
        "    - id: AC-1\n      status: covered\n"
        "    - id: AC-2\n      status: deferred\n      rationale: Observable only after merge.\n"
        f"{permit_line}"
        "---\n\n"
        "## Summary\n\n| ID | Status |\n| --- | --- |\n| AC-1 | covered |\n| AC-2 | deferred |\n\n"
        f"{sections}\n"
    )

REQUIREMENTS_GATE = (
    "gc.build.requirements.v1",
    "gc.build.requirements_path,gc.var.requirements_path",
)
PLAN_GATE = ("gc.build.plan.v1", "gc.build.plan_path,gc.var.plan_path")
DECOMPOSITION_GATE = (
    "gc.build.decomposition.v1",
    "gc.build.decomposition_path,gc.var.decomposition_path",
)
REVIEW_REPORT_GATE = (
    "gc.build.review.v1",
    "gc.build.review_report_path,gc.var.report_path",
)
FIX_LOOP_REVIEW_GATE = ("gc.build.review.v1", "gc.build.review_report_path")
BUILD_REVIEW_GATE = ("gc.build.review.v1", "gc.build.review_report_path")
FINAL_REPORT_GATE = ("gc.build.final-report.v1", "gc.build.final_report_path")
ROOT_IMPLEMENTATION_SUMMARY_GATE = (
    "gc.build.implementation-summary.v1",
    "gc.build.implementation_summary_path",
)
ITEM_SUMMARY_GATE = (
    "gc.build.implementation-summary.v1",
    "gc.implementation.summary_path,gc.build.implementation_summary_path,gc.var.summary_path",
)
AGGREGATE_SUMMARY_GATE = (
    "gc.build.implementation-summary.v1",
    "gc.implementation.summary_path,gc.var.summary_path",
)

# Producer stages that must keep an explicit build-artifact validation gate.
# Losing a row, the check wiring, or the repair bound is a contract regression.
BUILD_ARTIFACT_VALIDATION_GATES = {
    ("build-base", "requirements"): REQUIREMENTS_GATE,
    ("build-base", "plan"): PLAN_GATE,
    ("build-base", "decompose"): DECOMPOSITION_GATE,
    ("build-base", "summarize-implementation"): ROOT_IMPLEMENTATION_SUMMARY_GATE,
    ("build-base", "review"): BUILD_REVIEW_GATE,
    ("build-base", "finalize"): FINAL_REPORT_GATE,
    ("planning-base", "requirements"): REQUIREMENTS_GATE,
    ("planning-base", "plan"): PLAN_GATE,
    ("decomposition-base", "decompose"): DECOMPOSITION_GATE,
    ("code-review-base", "write-report"): REVIEW_REPORT_GATE,
    ("review", "write-report"): REVIEW_REPORT_GATE,
    ("fix-loop-base", "re-review"): FIX_LOOP_REVIEW_GATE,
    ("implementation-base", "implement"): ITEM_SUMMARY_GATE,
    ("do-work", "implement"): ITEM_SUMMARY_GATE,
    ("implementation-item-base", "implement-item"): ITEM_SUMMARY_GATE,
    ("do-work-item", "implement-item"): ITEM_SUMMARY_GATE,
    ("implement", "summarize"): AGGREGATE_SUMMARY_GATE,
    # Concrete and continuation overrides replace base steps wholesale
    # (mergeSteps replaces by ID), so every producer override must
    # re-declare its gate instead of assuming inheritance.
    ("build-basic", "requirements"): REQUIREMENTS_GATE,
    ("build-basic", "plan"): PLAN_GATE,
    ("build-basic", "decompose"): DECOMPOSITION_GATE,
    ("build-basic-review", "{target}"): BUILD_REVIEW_GATE,
    ("build-basic", "finalize"): FINAL_REPORT_GATE,
    ("build-from-requirements-base", "requirements"): REQUIREMENTS_GATE,
    ("build-from-plan-base", "plan"): PLAN_GATE,
    ("build-from-decompose-base", "decompose"): DECOMPOSITION_GATE,
    ("build-from-review-base", "review"): BUILD_REVIEW_GATE,
    ("build-from-review-base", "finalize"): FINAL_REPORT_GATE,
}

THIRD_PARTY_BUILD_PACKS = {
    "compound-engineering": {
        "formula": "compound-build",
        "base_import_binding": "gc",
        "base_import_source": "../gascity",
        "vendor": "compound-engineering-plugin",
        "upstream": "https://github.com/EveryInc/compound-engineering-plugin",
        "commit": "b6250490bec4c0488d68ad66d72bd99f6edb95fd",
        "implementation_target": "compound-engineering.ce-work",
        "planning_formula": "compound-planning",
        "decomposition_formula": "compound-decomposition",
        "implementation_entry_formula": "compound-implementation",
        "implementation_formula": "compound-work",
        "implementation_item_formula": "compound-work-item",
        "code_review_entry_formula": "compound-review",
        "review_fix_formula": "compound-fix-loop",
        "skills": {
            "requirements": "ce-brainstorm",
            "plan": "ce-plan",
            "implement": "ce-work",
            "review": "ce-code-review",
            "finalize": "ce-compound",
        },
        "expansions": {
            "plan-review": "compound-plan-review",
            "review": "compound-code-review",
            "finalize": "compound-resolution",
        },
        "review_expansion": "compound-code-review",
        "gap_analysis_target": "compound-engineering.ce-coherence-reviewer",
        "review_fix_asset": "assets/workflows/compound-code-review/{target}.apply-review-findings.md",
        "persona_assets": {
            "ce-architecture-strategist.md",
            "ce-adversarial-reviewer.md",
            "ce-agent-native-reviewer.md",
            "ce-api-contract-reviewer.md",
            "ce-coherence-reviewer.md",
            "ce-correctness-reviewer.md",
            "ce-data-migration-reviewer.md",
            "ce-deployment-verification-agent.md",
            "ce-feasibility-reviewer.md",
            "ce-julik-frontend-races-reviewer.md",
            "ce-learnings-researcher.md",
            "ce-maintainability-reviewer.md",
            "ce-performance-reviewer.md",
            "ce-pr-comment-resolver.md",
            "ce-previous-comments-reviewer.md",
            "ce-project-standards-reviewer.md",
            "ce-reliability-reviewer.md",
            "ce-scope-guardian-reviewer.md",
            "ce-security-reviewer.md",
            "ce-swift-ios-reviewer.md",
            "ce-testing-reviewer.md",
        },
    },
    "superpowers": {
        "formula": "superpowers-build",
        "base_import_binding": "gc",
        "base_import_source": "../gascity",
        "vendor": "superpowers",
        "upstream": "https://github.com/obra/superpowers",
        "commit": "6fd4507659784c351abbd2bc264c7162cfd386dc",
        "implementation_target": "superpowers.implementer",
        "planning_formula": "superpowers-planning",
        "decomposition_formula": "superpowers-decomposition",
        "implementation_entry_formula": "superpowers-implementation",
        "implementation_formula": "superpowers-development",
        "implementation_item_formula": "superpowers-development-item",
        "code_review_entry_formula": "superpowers-review",
        "review_fix_formula": "superpowers-fix-loop",
        "skills": {
            "requirements": "brainstorming",
            "plan": "writing-plans",
            "implement": "executing-plans",
            "review": "requesting-code-review",
            "finalize": "finishing-a-development-branch",
        },
        "expansions": {
            "requirements": "superpowers-brainstorming",
            "plan-review": "superpowers-plan-review",
            "review": "superpowers-code-review",
        },
        "review_expansion": "superpowers-code-review",
        "code_review_entry_expand_vars": {
            "artifact_path_keys": "gc.build.code_review_report_path,gc.build.review_report_path,gc.var.report_path",
        },
        "gap_analysis_target": "superpowers.code-quality-reviewer",
        "review_fix_asset": "assets/workflows/superpowers-code-review/{target}.process-code-review.md",
        "prompt_assets": {
            "skills/brainstorming/spec-document-reviewer-prompt.md",
            "skills/brainstorming/visual-companion.md",
            "skills/subagent-driven-development/spec-reviewer-prompt.md",
            "skills/subagent-driven-development/implementer-prompt.md",
            "skills/subagent-driven-development/code-quality-reviewer-prompt.md",
            "skills/requesting-code-review/code-reviewer.md",
            "skills/writing-plans/plan-document-reviewer-prompt.md",
        },
    },
    "bmad": {
        "formula": "bmad-build",
        "base_import_binding": "gc",
        "base_import_source": "../gascity",
        "vendor": "bmad-method",
        "upstream": "https://github.com/bmad-code-org/BMAD-METHOD",
        "commit": "072d0a74587ef1ea744d51f2dd4436ee2895758d",
        "implementation_target": "bmad.story-implementer",
        "planning_formula": "bmad-planning",
        "decomposition_formula": "bmad-decomposition",
        "implementation_entry_formula": "bmad-implementation",
        "implementation_formula": "bmad-story-development",
        "implementation_item_formula": "bmad-story-development-item",
        "code_review_entry_formula": "bmad-review",
        "review_fix_formula": "bmad-fix-loop",
        "skills": {
            "requirements": "bmad-prd",
            "plan": "bmad-create-architecture",
            "plan-review": "bmad-create-architecture",
            "implementation-readiness": "bmad-check-implementation-readiness",
            "decompose": "bmad-create-epics-and-stories",
            "implement": "bmad-quick-dev",
            "review": "bmad-code-review",
        },
        "extra_steps": ["implementation-readiness"],
        "expansions": {
            "review": "bmad-code-review-flow",
        },
        "review_expansion": "bmad-code-review-flow",
        "gap_analysis_target": "bmad.story-self-checker",
        "review_fix_asset": "assets/workflows/bmad-code-review-flow/{target}.apply-bmad-review-findings.md",
    },
    "gstack": {
        "formula": "gstack-build",
        "base_import_binding": "gc",
        "base_import_source": "../gascity",
        "vendor": "gstack",
        "upstream": "https://github.com/garrytan/gstack",
        "commit": "1626d4857bfe30da2690dd6a3217961934aa3192",
        "implementation_target": "gstack.implementer",
        "planning_formula": "gstack-planning",
        "decomposition_formula": "gstack-decomposition",
        "implementation_entry_formula": "gstack-implementation",
        "implementation_formula": "gstack-work",
        "implementation_item_formula": "gstack-work-item",
        "code_review_entry_formula": "gstack-review",
        "review_fix_formula": "gstack-fix-loop",
        "skills": {
            "requirements": "office-hours",
            "plan": "autoplan",
            "plan-review": "plan-eng-review",
            "implement": "ship",
            "review": "review",
            "finalize": "land-and-deploy",
        },
        "extra_steps": ["qa", "release-readiness"],
        "expansions": {
            "plan-review": "gstack-plan-review",
            "review": "gstack-code-review",
            "qa": "gstack-qa-review",
            "release-readiness": "gstack-release-readiness",
        },
        "review_expansion": "gstack-code-review",
        "review_expand_vars": {
            "review_mode": "{{review_mode}}",
        },
        "gap_analysis_target": "gstack.staff-reviewer",
        "review_fix_asset": "assets/workflows/gstack-code-review/{target}.apply-review-findings.md",
        "prompt_assets": {
            "skills/plan-ceo-review/SKILL.md",
            "skills/plan-design-review/SKILL.md",
            "skills/plan-devex-review/SKILL.md",
            "skills/qa/SKILL.md",
            "skills/cso/SKILL.md",
            "skills/document-release/SKILL.md",
            "skills/investigate/SKILL.md",
            "skills/spec/SKILL.md",
        },
    },
}


def methodology_selector_defaults(expected: dict) -> dict[str, str]:
    return {
        "planning_formula": expected["planning_formula"],
        "decomposition_formula": expected["decomposition_formula"],
        "implementation_formula": expected["implementation_entry_formula"],
        "implementation_item_formula": expected["implementation_item_formula"],
        "code_review_formula": expected["code_review_entry_formula"],
        "review_fix_formula": expected["review_fix_formula"],
    }


def load_formula(root: pathlib.Path, name: str) -> dict:
    return tomllib.loads((root / "formulas" / f"{name}.formula.toml").read_text(encoding="utf-8"))


def load_formula_from_dirs(formula_dirs: list[pathlib.Path], name: str) -> dict:
    for formula_dir in reversed(formula_dirs):
        path = formula_dir / f"{name}.formula.toml"
        if path.exists():
            return tomllib.loads(path.read_text(encoding="utf-8"))
    raise AssertionError(f"formula {name!r} not found in layered dirs")


def merged_steps(parent_steps: list[dict], child_steps: list[dict]) -> list[dict]:
    result = list(parent_steps)
    positions = {step["id"]: idx for idx, step in enumerate(result)}
    for step in child_steps:
        idx = positions.get(step["id"])
        if idx is None:
            positions[step["id"]] = len(result)
            result.append(step)
        else:
            result[idx] = step
    return result


def resolve_formula(root: pathlib.Path, name: str, seen: tuple[str, ...] = ()) -> dict:
    if name in seen:
        raise AssertionError(f"circular formula extends: {' -> '.join((*seen, name))}")
    data = load_formula(root, name)
    parents = data.get("extends", [])
    if not parents:
        return data

    merged: dict = {
        "formula": data["formula"],
        "description": data.get("description", ""),
        "version": data.get("version", 1),
        "contract": data.get("contract", ""),
        "requires": data.get("requires", {}),
        "target_required": data.get("target_required"),
        "vars": {},
        "steps": [],
    }
    for parent in parents:
        parent_data = resolve_formula(root, parent, (*seen, name))
        if not merged["contract"]:
            merged["contract"] = parent_data.get("contract", "")
        if not merged["requires"]:
            merged["requires"] = parent_data.get("requires", {})
        if merged["target_required"] is None:
            merged["target_required"] = parent_data.get("target_required")
        merged["vars"].update(parent_data.get("vars", {}))
        merged["steps"].extend(parent_data.get("steps", []))

    merged["vars"].update(data.get("vars", {}))
    merged["steps"] = merged_steps(merged["steps"], data.get("steps", []))
    if data.get("description"):
        merged["description"] = data["description"]
    return merged


def resolve_formula_from_dirs(formula_dirs: list[pathlib.Path], name: str, seen: tuple[str, ...] = ()) -> dict:
    if name in seen:
        raise AssertionError(f"circular formula extends: {' -> '.join((*seen, name))}")
    data = load_formula_from_dirs(formula_dirs, name)
    parents = data.get("extends", [])
    if not parents:
        return data

    merged: dict = {
        "formula": data["formula"],
        "description": data.get("description", ""),
        "version": data.get("version", 1),
        "contract": data.get("contract", ""),
        "target_required": data.get("target_required"),
        "vars": {},
        "steps": [],
    }
    for parent in parents:
        parent_data = resolve_formula_from_dirs(formula_dirs, parent, (*seen, name))
        if not merged["contract"]:
            merged["contract"] = parent_data.get("contract", "")
        if merged["target_required"] is None:
            merged["target_required"] = parent_data.get("target_required")
        merged["vars"].update(parent_data.get("vars", {}))
        merged["steps"].extend(parent_data.get("steps", []))

    merged["vars"].update(data.get("vars", {}))
    merged["steps"] = merged_steps(merged["steps"], data.get("steps", []))
    if data.get("description"):
        merged["description"] = data["description"]
    return merged


def effective_formula_text(root: pathlib.Path, name: str) -> str:
    data = load_formula(root, name)
    chunks = []
    for parent in data.get("extends", []):
        chunks.append(effective_formula_text(root, parent))
    formula_path = root / "formulas" / f"{name}.formula.toml"
    chunks.append(formula_path.read_text(encoding="utf-8"))
    for node in formula_nodes(data):
        description_file = node.get("description_file")
        if description_file:
            chunks.append((formula_path.parent / description_file).resolve().read_text(encoding="utf-8"))
    return "\n".join(chunks)


def effective_formula_text_from_dirs(formula_dirs: list[pathlib.Path], name: str) -> str:
    data = load_formula_from_dirs(formula_dirs, name)
    chunks = []
    for parent in data.get("extends", []):
        chunks.append(effective_formula_text_from_dirs(formula_dirs, parent))

    formula_path = None
    for formula_dir in reversed(formula_dirs):
        candidate = formula_dir / f"{name}.formula.toml"
        if candidate.exists():
            formula_path = candidate
            break
    if formula_path is None:
        raise AssertionError(f"formula {name!r} not found in layered dirs")

    chunks.append(formula_path.read_text(encoding="utf-8"))
    for node in formula_nodes(data):
        description_file = node.get("description_file")
        if description_file:
            chunks.append((formula_path.parent / description_file).resolve().read_text(encoding="utf-8"))
    return "\n".join(chunks)


def formula_nodes(data: dict) -> list[dict]:
    nodes = list(data.get("steps", []))
    for step in data.get("steps", []):
        nodes.extend(step.get("children", []))
    nodes.extend(data.get("template", []))
    for template in data.get("template", []):
        nodes.extend(template.get("children", []))
    return nodes


def node_description(root: pathlib.Path, node: dict) -> str:
    description_file = node.get("description_file")
    if description_file:
        return (root / "formulas" / description_file).resolve().read_text(encoding="utf-8")
    return node["description"]


def rendered_step_text(root: pathlib.Path, formula: str, step_id: str) -> str:
    """Prose a step actually renders after extends/override resolution.

    Unlike effective_formula_text, overridden parent prose is excluded, so a
    fragment found here is one the worker really sees.
    """
    steps = {step["id"]: step for step in resolve_formula(root, formula)["steps"]}
    return node_description(root, steps[step_id])


def drained_formula_text(root: pathlib.Path, formula: str, step_id: str) -> str:
    """Rendered prose of the formula a drain step dispatches per work item."""
    steps = {step["id"]: step for step in resolve_formula(root, formula)["steps"]}
    drained = resolve_formula(root, steps[step_id]["drain"]["formula"])
    return "\n".join(node_description(root, step) for step in drained["steps"])


def drained_step_text(root: pathlib.Path, formula: str, step_id: str, drained_step_id: str) -> str:
    """Rendered prose of one step in the formula a drain step dispatches."""
    steps = {step["id"]: step for step in resolve_formula(root, formula)["steps"]}
    drained = resolve_formula(root, steps[step_id]["drain"]["formula"])
    return node_description(root, {step["id"]: step for step in drained["steps"]}[drained_step_id])


def expansion_node_text(root: pathlib.Path, formula: str, step_id: str, suffix: str) -> str:
    """Rendered prose of one node in the expansion a step expands into."""
    steps = {step["id"]: step for step in resolve_formula(root, formula)["steps"]}
    for node in formula_nodes(load_formula(root, steps[step_id]["expand"])):
        if node["id"].endswith(suffix):
            return node_description(root, node)
    raise AssertionError(f"{formula}.{step_id} expansion has no node ending {suffix!r}")


def repair_step_text(root: pathlib.Path, formula: str) -> str:
    """Prose of the step that applies review findings for a build-* formula."""
    if formula == "build-basic":
        return expansion_node_text(root, formula, "review", ".apply-review-findings")
    return rendered_step_text(root, formula, "repair-review")


def route_target_default(target: str, vars: dict) -> str:
    if target.startswith("{{") and target.endswith("}}"):
        var_name = target.removeprefix("{{").removesuffix("}}").strip()
        if var_name not in vars:
            raise AssertionError(f"templated route target {target!r} has no matching formula var")
        default = vars[var_name].get("default", "")
        if not default:
            raise AssertionError(f"templated route target {target!r} var has no default")
        return default
    if target.startswith("{") and target.endswith("}"):
        var_name = target.removeprefix("{").removesuffix("}").strip()
        if var_name not in vars:
            raise AssertionError(f"expansion route target {target!r} has no matching formula var")
        default = vars[var_name].get("default", "")
        if not default:
            raise AssertionError(f"expansion route target {target!r} var has no default")
        return default
    return target


def assert_role_route_target(test_case: unittest.TestCase, target: str, vars: dict) -> None:
    resolved = route_target_default(target, vars)
    test_case.assertTrue(resolved.startswith("gc."))
    test_case.assertIn(resolved.removeprefix("gc."), ROLE_AGENTS)
    test_case.assertNotIn("workflows.", resolved)


def assert_pack_or_role_route_target(
    test_case: unittest.TestCase,
    target: str,
    vars: dict,
    pack_root: pathlib.Path,
    pack_name: str,
) -> None:
    resolved = route_target_default(target, vars)
    if resolved.startswith("gc."):
        test_case.assertIn(resolved.removeprefix("gc."), ROLE_AGENTS)
        return

    prefix = f"{pack_name}."
    test_case.assertTrue(resolved.startswith(prefix), f"{resolved!r} must target {prefix}* or gc.*")
    local_agent = resolved.removeprefix(prefix)
    test_case.assertTrue((pack_root / "agents" / local_agent / "agent.toml").is_file())


def write_check_gc_stub(bin_dir: pathlib.Path, *, parent_show: bool = False) -> pathlib.Path:
    """Write a fake `gc` for the check-script tests, and return its path.

    The check gates enumerate molecule members with `gc ready` (one leg per
    --status) rather than a metadata-filtered `gc bd list`, because a collection
    query carries no bead id and is refused on a city that relocates the graph
    class. The stub answers every `ready` leg from BD_LIST_JSON, so the union
    the gate builds is the same member set the old single list call returned.

    `ready` without --metadata-field is rejected: unscoped, it would return the
    whole city and the gate could read a *different* molecule's verdict. Any
    other verb exits 2, so a gate that starts shelling out to something new
    fails here instead of silently reading an empty set.

    A member carrying an explicit "status" is served only by that status's leg;
    a member without one is served by every leg (the gate dedupes by id). That
    lets a test place a bead in exactly one leg and prove the gate unions all
    four — without disturbing the fixtures that don't model status at all.
    """
    show = (
        "    if [ \"${2:-}\" = \"root\" ]; then\n"
        "      cat \"$BD_PARENT_SHOW_JSON\"\n"
        "    else\n"
        "      cat \"$BD_SHOW_JSON\"\n"
        "    fi\n"
        if parent_show
        else "    cat \"$BD_SHOW_JSON\"\n"
    )
    fake_gc = bin_dir / "gc"
    fake_gc.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [ \"${1:-}\" = \"ready\" ]; then\n"
        "  case \" $* \" in\n"
        "    *\" --metadata-field \"*) : ;;\n"
        "    *) echo \"stub: gc ready without --metadata-field\" >&2; exit 2 ;;\n"
        "  esac\n"
        "  st=\"\"\n"
        "  while [ \"$#\" -gt 0 ]; do\n"
        "    if [ \"$1\" = \"--status\" ]; then st=\"${2:-}\"; break; fi\n"
        "    shift\n"
        "  done\n"
        "  if [ -z \"$st\" ]; then echo \"stub: gc ready without --status\" >&2; exit 2; fi\n"
        "  jq --arg st \"$st\" 'map(select((.status // $st) == $st))' \"$BD_LIST_JSON\"\n"
        "  exit 0\n"
        "fi\n"
        "while [ \"${1:-}\" != \"bd\" ]; do shift; done\n"
        "shift\n"
        "case \"$1\" in\n"
        "  version) exit 0 ;;\n"
        "  show)\n" + show + "    ;;\n"
        "  list) cat \"$BD_LIST_JSON\" ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_gc.chmod(0o755)
    return fake_gc


class FormulaAssetTests(unittest.TestCase):
    def test_expected_formula_set_is_convoy_first(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        paths = sorted((root / "formulas").glob("*.formula.toml"))

        self.assertEqual({path.name.removesuffix(".formula.toml") for path in paths}, FORMULAS)
        for path in paths:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            name = path.name.removesuffix(".formula.toml")
            self.assertEqual(data["formula"], name)
            self.assertEqual(data["requires"]["formula_compiler"], ">=2.0.0")
            var_names = set(data.get("vars", {}))
            self.assertNotIn("issue", var_names)
            self.assertNotIn("bead_id", var_names)
            self.assertNotIn("convoy_id", var_names, f"{path.name} must not redeclare reserved convoy_id")

    def test_expected_role_agents_are_providerless(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        roles_pack = tomllib.loads((root / "roles" / "pack.toml").read_text(encoding="utf-8"))
        paths = sorted((root / "roles" / "agents").glob("*/agent.toml"))

        self.assertEqual(roles_pack["pack"]["name"], "gc-roles")
        self.assertEqual({path.parent.name for path in paths}, ROLE_AGENTS)
        for path in paths:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            # implementation-worker is deliberately unscoped (city + rig
            # expansion contexts) so city-scope beads route to the same
            # worker role — one worker pattern, no separate city pool
            # (operator decision 2026-08-20). All other roles stay rig-scoped.
            if path.parent.name == "implementation-worker":
                self.assertNotIn("scope", data)
            else:
                self.assertEqual(data["scope"], "rig")
            self.assertTrue(data["fallback"])
            self.assertNotIn("provider", data, f"{path} must inherit the city/workspace provider by default")
            self.assertTrue((path.parent / "prompt.template.md").is_file())
        self.assertIn(root / "roles" / "agents" / "run-operator" / "agent.toml", paths)

    def test_role_agent_prompts_embed_shared_claim_protocol(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        fragment = root / "fragments" / "template-fragments" / "gc-role-worker.template.md"
        text = fragment.read_text(encoding="utf-8")
        include = '{{ template "gc-role-worker" . }}'

        for required in (
            "only work-discovery command",
            "already assigned work before dying",
            "gc hook --claim --drain-ack --json",
            "`gc bd mol current`",
            "CLAIMED_BEAD_ID",
            "CLAIMED_ROOT_BEAD_ID",
            "CLAIMED_CONTINUATION_GROUP",
            "gc runtime drain-ack",
            'an empty value never means "no more work"',
            "Never ask a human whether to proceed after a successful claim",
            "authoritative stop/go signal",
            "Set required metadata before closing same claimed bead",
            'gc bd update "$CLAIMED_BEAD_ID"',
            'gc bd close "$CLAIMED_BEAD_ID"',
            "Review findings, missing tests, or follow-up usually are output",
            "After close, claim again immediately",
            'Never claim "drained" without acknowledgement',
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertNotIn("GC_CLAIM", text)

        for agent_name in ROLE_AGENTS:
            prompt = root / "roles" / "agents" / agent_name / "prompt.template.md"
            with self.subTest(agent=agent_name):
                self.assertEqual(prompt.read_text(encoding="utf-8"), f"{include}\n")

    def test_shared_claim_protocol_documents_failure_class_vocabulary(self) -> None:
        # gc-32cmg: only the literal values "hard" (or unset) and
        # "transient" are dispatcher-recognized (internal/dispatch/retry.go
        # classifyRetryAttempt); any other string a worker writes for a
        # structural, retry-proof blocker (e.g. "structural",
        # "blocked-prerequisite") falls through to the "malformed
        # contract" default and gets silently retried anyway, burning the
        # attempt budget re-deriving the same diagnosis (confirmed
        # incidents: formula-flow ff-m03/ff-vwis/ff-vw0p and ascent
        # gcas-unwt4b/gcas-lsbmw1/gcas-2j2m99). Workers must be told the
        # exact recognized vocabulary, not left to guess.
        root = pathlib.Path(__file__).resolve().parents[1]
        fragment = root / "fragments" / "template-fragments" / "gc-role-worker.template.md"
        text = fragment.read_text(encoding="utf-8")
        for required in (
            "dispatcher-recognized values",
            "`hard` (or leaving the field unset/empty)",
            "`transient` — retryable",
            "not recognized vocabulary",
            "the literal value must be `hard`",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_city_claim_command_verifies_and_normalizes_claim(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        command = root / "commands" / "claim" / "run.sh"

        self.assertTrue(command.is_file())
        self.assertTrue(command.stat().st_mode & 0o111)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = hook ] && [ \"$2\" = --claim ] && [ \"$3\" = --drain-ack ] && [ \"$4\" = --json ]; then\n"
                "  printf '%s\\n' '{\"action\":\"work\",\"bead_id\":\"bd-123\",\"assignee\":\"worker\",\"route\":\"gc.implementation-worker\"}'\n"
                "elif [ \"$1\" = bd ] && [ \"$2\" = show ] && [ \"$3\" = bd-123 ] && [ \"$4\" = --json ]; then\n"
                "  printf '%s\\n' '{\"id\":\"bd-123\",\"status\":\"in_progress\",\"assignee\":\"worker\",\"metadata\":{\"gc.routed_to\":\"gc.implementation-worker\",\"gc.root_bead_id\":\"root-1\",\"gc.continuation_group\":\"group-1\"}}'\n"
                "else\n"
                "  exit 2\n"
                "fi\n",
                encoding="utf-8",
            )
            fake_gc.chmod(0o755)
            env = {
                **os.environ,
                "BEADS_ACTOR": "worker",
                "GC_AGENT": "gc.implementation-worker",
                # commands/claim/run.sh reads `${GC_TEMPLATE:-${GC_AGENT:-}}`, so
                # a seat's exported GC_TEMPLATE outranks the GC_AGENT this test is
                # exercising and the claim is rejected on a route mismatch. Empty
                # falls through to GC_AGENT; the env dict cannot unset a name.
                "GC_TEMPLATE": "",
                "GC_PACK_DIR": str(root),
                "GC_PACK_NAME": "gc",
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            }
            result = subprocess.run([str(command)], capture_output=True, env=env, text=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "action": "work",
                "bead_id": "bd-123",
                "root_bead_id": "root-1",
                "continuation_group": "group-1",
                "bead": {
                    "id": "bd-123",
                    "status": "in_progress",
                    "assignee": "worker",
                    "metadata": {
                        "gc.routed_to": "gc.implementation-worker",
                        "gc.root_bead_id": "root-1",
                        "gc.continuation_group": "group-1",
                    },
                },
            },
        )

    def test_city_claim_command_returns_drain_without_bead_lookup(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        command = root / "commands" / "claim" / "run.sh"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = hook ] && [ \"$2\" = --claim ] && [ \"$3\" = --drain-ack ] && [ \"$4\" = --json ]; then\n"
                "  printf '%s\\n' '{\"action\":\"drain\"}'\n"
                "else\n"
                "  exit 2\n"
                "fi\n",
                encoding="utf-8",
            )
            fake_gc.chmod(0o755)
            env = {
                **os.environ,
                "BEADS_ACTOR": "worker",
                "GC_PACK_DIR": str(root),
                "GC_PACK_NAME": "gc",
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            }
            result = subprocess.run([str(command)], capture_output=True, env=env, text=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"action": "drain"})

    def test_city_claim_command_declares_authoritative_convoy_source_on_explicit_run(
        self,
    ) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        command = root / "commands" / "claim" / "run.sh"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            gc_calls = tmp_path / "gc-calls"
            observer_calls = tmp_path / "observer-calls"
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$GC_TEST_CALLS\"\n"
                "if [ \"$1\" = hook ]; then\n"
                "  printf '%s\\n' '{\"action\":\"work\",\"bead_id\":\"gcg-step\",\"assignee\":\"worker\",\"route\":\"gc.implementation-worker\"}'\n"
                "elif [ \"$1\" = bd ] && [ \"$2\" = show ] && [ \"$3\" = gcg-step ]; then\n"
                "  printf '%s\\n' '{\"id\":\"gcg-step\",\"status\":\"in_progress\",\"assignee\":\"worker\",\"metadata\":{\"gc.routed_to\":\"gc.implementation-worker\",\"gc.input_convoy_id\":\"gcg-input\"}}'\n"
                "elif [ \"$1\" = bd ] && [ \"$2\" = update ] && [ \"$3\" = session-1 ]; then\n"
                "  exit 0\n"
                "elif [ \"$1\" = convoy ] && [ \"$2\" = status ] && [ \"$3\" = gcg-input ]; then\n"
                "  printf '%s\\n' '{\"schema_version\":\"1\",\"convoy\":{\"id\":\"gcg-input\"},\"children\":[{\"id\":\"ga-source-anchor\"},{\"id\":\"ga-rig-anchor\"}]}'\n"
                "elif [ \"$1\" = bd ] && [ \"$2\" = show ] && [ \"$3\" = ga-source-anchor ]; then\n"
                "  printf '%s\\n' '{\"id\":\"ga-source-anchor\",\"metadata\":{\"gc.source_store_ref\":\"city:\",\"gc.source_bead_id\":\"mc-tawl\"}}'\n"
                "elif [ \"$1\" = bd ] && [ \"$2\" = show ] && [ \"$3\" = ga-rig-anchor ]; then\n"
                "  printf '%s\\n' '{\"id\":\"ga-rig-anchor\",\"metadata\":{\"gc.source_store_ref\":\"rig:gascity\",\"gc.source_bead_id\":\"ga-local\"}}'\n"
                "else\n"
                "  exit 2\n"
                "fi\n",
                encoding="utf-8",
            )
            fake_gc.chmod(0o755)
            fake_observer = bin_dir / "gasworks-observer"
            fake_observer.write_text(
                "#!/bin/sh\n"
                "printf '%s|%s\\n' \"${GASWORKS_RUN_ID:-}\" \"$*\" >>\"$OBSERVER_TEST_CALLS\"\n",
                encoding="utf-8",
            )
            fake_observer.chmod(0o755)
            env = {
                **os.environ,
                "BEADS_ACTOR": "worker",
                "GC_AGENT": "gc.implementation-worker",
                "GC_TEMPLATE": "",  # see the GC_TEMPLATE note above
                "GC_PACK_DIR": str(root),
                "GC_PACK_NAME": "gc",
                "GC_SESSION_ID": "session-1",
                "GC_BEADS_PROJECT_ID": "prj_343030dd09cda2fb",
                "GASWORKS_RUN_ID": "gwr_explicit",
                "GASWORKS_OBSERVER_BIN": str(fake_observer),
                "GC_TEST_CALLS": str(gc_calls),
                "OBSERVER_TEST_CALLS": str(observer_calls),
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            }
            result = subprocess.run([str(command)], capture_output=True, env=env, text=True)
            call_lines = gc_calls.read_text(encoding="utf-8").splitlines()
            observer_lines = (
                observer_calls.read_text(encoding="utf-8").splitlines()
                if observer_calls.exists()
                else []
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["bead_id"], "gcg-step")
        self.assertIn(
            " ".join(
                (
                    "b" + "d",
                    "update",
                    "session-1",
                    "--set-metadata",
                    "gc.current_run_id=gwr_explicit",
                )
            ),
            call_lines,
        )
        self.assertIn("convoy status gcg-input --json", call_lines)
        self.assertIn(" ".join(("b" + "d", "show", "ga-source-anchor", "--json")), call_lines)
        self.assertIn(" ".join(("b" + "d", "show", "ga-rig-anchor", "--json")), call_lines)
        self.assertEqual(
            observer_lines,
            [
                "gwr_explicit|declare-work -beads-project "
                "prj_343030dd09cda2fb -work-item mc-tawl"
            ],
        )

    def test_city_claim_command_bounds_ambiguous_hook_failures_without_drain_ack(
        self,
    ) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        command = root / "commands" / "claim" / "run.sh"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            calls = tmp_path / "calls"
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$GC_TEST_CALLS\"\n"
                "if [ \"$1\" = hook ]; then\n"
                "  printf '%s\\n' '{\"action\":\"drain\"}'\n"
                "  echo 'permanent hook failure' >&2\n"
                "  exit 7\n"
                "fi\n"
                "if [ \"$1\" = runtime ] && [ \"$2\" = drain-ack ]; then\n"
                "  exit 0\n"
                "fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            fake_gc.chmod(0o755)
            fake_sleep = bin_dir / "sleep"
            fake_sleep.write_text("#!/bin/sh\n/bin/sleep 0.05\n", encoding="utf-8")
            fake_sleep.chmod(0o755)
            env = {
                **os.environ,
                "BEADS_ACTOR": "worker",
                "GC_AGENT": "gc.implementation-worker",
                "GC_PACK_DIR": str(root),
                "GC_PACK_NAME": "gc",
                "GC_TEST_CALLS": str(calls),
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            }
            result = subprocess.run(
                [str(command)], capture_output=True, env=env, text=True, timeout=10
            )
            call_lines = calls.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 1)
        self.assertIn("permanent hook failure", result.stderr)
        self.assertIn("after 3 attempts", result.stderr)
        self.assertEqual(call_lines.count("hook --claim --drain-ack --json"), 3)
        self.assertNotIn("runtime drain-ack", call_lines)

    def test_city_claim_command_drain_acks_missing_assignee_configuration(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        command = root / "commands" / "claim" / "run.sh"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            calls = tmp_path / "calls"
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$GC_TEST_CALLS\"\n"
                "if [ \"$1\" = runtime ] && [ \"$2\" = drain-ack ]; then exit 0; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            fake_gc.chmod(0o755)
            env = {
                **os.environ,
                "GC_PACK_DIR": str(root),
                "GC_PACK_NAME": "gc",
                "GC_TEST_CALLS": str(calls),
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            }
            for key in ("BEADS_ACTOR", "GC_SESSION_NAME", "GC_SESSION_ID", "GC_AGENT"):
                env.pop(key, None)
            result = subprocess.run([str(command)], capture_output=True, env=env, text=True)
            call_lines = calls.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("CONFIG_REJECTED", result.stderr)
        self.assertEqual(call_lines, ["runtime drain-ack"])

    def test_city_claim_command_drain_acks_missing_python_configuration(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        command = root / "commands" / "claim" / "run.sh"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            calls = tmp_path / "calls"
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$GC_TEST_CALLS\"\n"
                "if [ \"$1\" = runtime ] && [ \"$2\" = drain-ack ]; then exit 0; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            fake_gc.chmod(0o755)
            env = {
                **os.environ,
                "BEADS_ACTOR": "worker",
                "GC_PACK_DIR": str(root),
                "GC_PACK_NAME": "gc",
                "GC_TEST_CALLS": str(calls),
                "PATH": str(bin_dir),
            }
            result = subprocess.run([str(command)], capture_output=True, env=env, text=True)
            call_lines = calls.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("CONFIG_REJECTED", result.stderr)
        self.assertEqual(call_lines, ["runtime drain-ack"])

    def test_city_claim_command_reports_failed_drain_ack(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        command = root / "commands" / "claim" / "run.sh"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            calls = tmp_path / "calls"
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$GC_TEST_CALLS\"\n"
                "if [ \"$1\" = runtime ] && [ \"$2\" = drain-ack ]; then exit 9; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            fake_gc.chmod(0o755)
            env = {
                **os.environ,
                "GC_PACK_DIR": str(root),
                "GC_PACK_NAME": "gc",
                "GC_TEST_CALLS": str(calls),
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            }
            for key in ("BEADS_ACTOR", "GC_SESSION_NAME", "GC_SESSION_ID", "GC_AGENT"):
                env.pop(key, None)
            result = subprocess.run([str(command)], capture_output=True, env=env, text=True)
            call_lines = calls.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 1)
        self.assertIn("CONFIG_REJECTED", result.stderr)
        self.assertIn("DRAIN_ACK_FAILED", result.stderr)
        self.assertEqual(call_lines, ["runtime drain-ack"])

    def test_city_claim_command_termination_signal_stops_before_retry(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        command = root / "commands" / "claim" / "run.sh"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            calls = tmp_path / "calls"
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$GC_TEST_CALLS\"\n"
                "if [ \"$1\" = hook ]; then\n"
                "  kill -TERM \"$PPID\"\n"
                "  exit 7\n"
                "fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            fake_gc.chmod(0o755)
            env = {
                **os.environ,
                "BEADS_ACTOR": "worker",
                "GC_AGENT": "gc.implementation-worker",
                "GC_PACK_DIR": str(root),
                "GC_PACK_NAME": "gc",
                "GC_TEST_CALLS": str(calls),
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            }
            result = subprocess.run(
                [str(command)], capture_output=True, env=env, text=True, timeout=2
            )
            call_lines = calls.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertEqual(call_lines, ["hook --claim --drain-ack --json"])

    def test_city_claim_command_preserves_owned_route_mismatch_for_recovery(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        command = root / "commands" / "claim" / "run.sh"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            calls = tmp_path / "calls"
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$GC_TEST_CALLS\"\n"
                "if [ \"$1\" = hook ]; then\n"
                "  printf '%s\\n' '{\"action\":\"work\",\"bead_id\":\"bd-123\",\"assignee\":\"worker\",\"route\":\"gc.wrong-worker\"}'\n"
                "elif [ \"$1\" = bd ] && [ \"$2\" = show ]; then\n"
                "  printf '%s\\n' '{\"id\":\"bd-123\",\"status\":\"in_progress\",\"assignee\":\"worker\",\"metadata\":{\"gc.routed_to\":\"gc.wrong-worker\",\"gc.root_bead_id\":\"root-1\",\"gc.continuation_group\":\"group-1\"}}'\n"
                "elif [ \"$1\" = bd ] && [ \"$2\" = update ]; then\n"
                "  exit 0\n"
                "elif [ \"$1\" = runtime ] && [ \"$2\" = drain-ack ]; then\n"
                "  exit 0\n"
                "else\n"
                "  exit 2\n"
                "fi\n",
                encoding="utf-8",
            )
            fake_gc.chmod(0o755)
            fake_sleep = bin_dir / "sleep"
            fake_sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_sleep.chmod(0o755)
            env = {
                **os.environ,
                "BEADS_ACTOR": "worker",
                "GC_AGENT": "gc.implementation-worker",
                "GC_PACK_DIR": str(root),
                "GC_PACK_NAME": "gc",
                "GC_TEST_CALLS": str(calls),
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            }
            result = subprocess.run(
                [str(command)],
                capture_output=True,
                env=env,
                text=True,
            )
            call_lines = calls.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(call_lines.count("hook --claim --drain-ack --json"), 1)
        self.assertEqual(
            call_lines.count(" ".join(("b" + "d", "show", "bd-123", "--json"))),
            1,
        )
        self.assertEqual(len(call_lines), 2)

    def test_city_claim_command_preserves_unreadable_claim_after_bounded_retries(
        self,
    ) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        command = root / "commands" / "claim" / "run.sh"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            calls = tmp_path / "calls"
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >>\"$GC_TEST_CALLS\"\n"
                "if [ \"$1\" = hook ]; then\n"
                "  printf '%s\\n' '{\"action\":\"work\",\"bead_id\":\"bd-123\",\"assignee\":\"worker\",\"route\":\"gc.implementation-worker\"}'\n"
                "  exit 0\n"
                "elif [ \"$1\" = bd ] && [ \"$2\" = show ]; then\n"
                "  printf '%s\\n' '{}'\n"
                "  exit 0\n"
                "elif [ \"$1\" = bd ] && [ \"$2\" = update ]; then\n"
                "  exit 0\n"
                "elif [ \"$1\" = runtime ] && [ \"$2\" = drain-ack ]; then\n"
                "  exit 0\n"
                "fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            fake_gc.chmod(0o755)
            fake_sleep = bin_dir / "sleep"
            fake_sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_sleep.chmod(0o755)
            env = {
                **os.environ,
                "BEADS_ACTOR": "worker",
                "GC_AGENT": "gc.implementation-worker",
                "GC_PACK_DIR": str(root),
                "GC_PACK_NAME": "gc",
                "GC_TEST_CALLS": str(calls),
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            }
            result = subprocess.run(
                [str(command)], capture_output=True, env=env, text=True, timeout=2
            )
            call_lines = calls.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("incomplete bead record", result.stderr)
        self.assertEqual(call_lines.count("hook --claim --drain-ack --json"), 1)
        show_call = " ".join(("b" + "d", "show", "bd-123", "--json"))
        self.assertEqual(call_lines.count(show_call), 3)
        self.assertEqual(len(call_lines), 4)


    def test_third_party_agents_include_work_claim_protocol(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[2]
        include = '{{ template "gc-role-worker" . }}'

        for pack_name in THIRD_PARTY_BUILD_PACKS:
            prompts = sorted((root / pack_name / "agents").glob("*/prompt.template.md"))
            self.assertGreater(len(prompts), 0, f"{pack_name} must define agent prompts")
            for prompt in prompts:
                with self.subTest(pack=pack_name, agent=prompt.parent.name):
                    text = prompt.read_text(encoding="utf-8")
                    self.assertIn(include, text)
                    self.assertEqual(text.count(include), 1)

    def test_formula_route_targets_are_backed_by_providerless_role_agents(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        for path in sorted((root / "formulas").glob("*.formula.toml")):
            name = path.name.removesuffix(".formula.toml")
            data = resolve_formula(root, name)
            for step in data.get("steps", []):
                target = step.get("metadata", {}).get("gc.run_target", "")
                if not target:
                    continue
                with self.subTest(formula=path.name, step=step["id"], target=target):
                    assert_role_route_target(self, target, data.get("vars", {}))

    def test_formula_catalog_metadata_marks_user_runnable_workflows(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        catalog_names: set[str] = set()
        for path in sorted((root / "formulas").glob("*.formula.toml")):
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            name = path.name.removesuffix(".formula.toml")
            catalog = data.get("catalog")
            if catalog is None:
                continue
            with self.subTest(formula=name):
                self.assertEqual(catalog["name"], name)
                self.assertIsInstance(catalog.get("description"), str)
                self.assertGreater(len(catalog["description"].strip()), 0)
            catalog_names.add(name)

        self.assertEqual(catalog_names, CATALOG_FORMULAS)

    def test_base_formula_requirements_cover_formula_set(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        pack_ledger = (root / "REQUIREMENTS.md").read_text(encoding="utf-8")
        formula_ledger = (root / "formulas" / "REQUIREMENTS.md").read_text(encoding="utf-8")

        self.assertIn("gc.build-methodology-base.requirements.v1", pack_ledger)
        self.assertIn("gc.base-formulas.requirements.v1", formula_ledger)
        for name in sorted(FORMULAS):
            with self.subTest(formula=name):
                self.assertRegex(
                    formula_ledger,
                    rf"\|\s*GC-BF-\d{{3}}\s*\|\s*`{re.escape(name)}`\s*\|",
                )

        for name in ("build-base", "build-basic", "planning-base", "fix-loop-base"):
            with self.subTest(pack_ledger=name):
                self.assertIn(name, pack_ledger)

    def test_methodology_stage_contracts_are_virtual_and_shadowable(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        for name, expected in METHODOLOGY_STAGE_CONTRACTS.items():
            with self.subTest(formula=name):
                data = load_formula(root, name)
                self.assertEqual(data["formula"], name)
                self.assertEqual(data["requires"]["formula_compiler"], ">=2.0.0")
                self.assertTrue(data["internal"])
                self.assertNotIn("catalog", data)
                self.assertNotIn("extends", data)
                self.assertEqual(data["target_required"], expected["target_required"])
                self.assertEqual(set(data.get("vars", {})), expected["vars"])
                self.assertEqual([step["id"] for step in data["steps"]], expected["steps"])
                if "mode" in expected:
                    self.assertEqual(data["mode"], expected["mode"])

                text = effective_formula_text(root, name)
                self.assertIn("methodology contract", text)
                self.assertIn(name, text)
                for step in data["steps"]:
                    description = node_description(root, step)
                    with self.subTest(formula=name, step=step["id"]):
                        self.assertIn("override", description.lower())

    def test_core_formulas_extend_smaller_methodology_contracts(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        expected_extends = {
            "do-work": ["implementation-base"],
            "do-work-item": ["implementation-item-base"],
            "review": ["code-review-base"],
        }
        for name, parents in expected_extends.items():
            with self.subTest(formula=name):
                data = load_formula(root, name)
                resolved = resolve_formula(root, name)
                parent = load_formula(root, parents[0])
                self.assertEqual(data["extends"], parents)
                self.assertEqual([step["id"] for step in resolved["steps"]], [step["id"] for step in parent["steps"]])

    def test_entrypoint_adapters_expose_methodology_formula_vars(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        expected_by_formula = {
            "build-base": {
                **METHODOLOGY_FORMULA_VARS,
                "drain_policy": "separate",
                **MODE_VAR_DEFAULTS["build-base"],
            },
            "github-pr-review": {
                "code_review_formula": METHODOLOGY_FORMULA_VARS["code_review_formula"],
                **MODE_VAR_DEFAULTS["github-pr-review"],
            },
            "github-issue-fix-base": {
                **METHODOLOGY_FORMULA_VARS,
                "drain_policy": "separate",
                **MODE_VAR_DEFAULTS["github-issue-fix-base"],
            },
        }
        for name, expected_vars in expected_by_formula.items():
            data = load_formula(root, name)
            text = effective_formula_text(root, name)
            for var_name, default in expected_vars.items():
                with self.subTest(formula=name, var=var_name):
                    self.assertIn(var_name, data["vars"])
                    self.assertEqual(data["vars"][var_name]["default"], default)
                    self.assertIn(f"{{{{{var_name}}}}}", text)

        alias = load_formula(root, "github-issue-fix-base")["vars"]["mode"]
        self.assertEqual(alias["default"], "interactive")
        self.assertIn("alias", alias["description"])
        self.assertIn("gc.var.interaction_mode", alias["description"])

    def test_top_level_build_formulas_declare_methodology_metadata(self) -> None:
        gascity_root = pathlib.Path(__file__).resolve().parents[1]
        packs_root = gascity_root.parent
        for name, pack_dir in TOP_LEVEL_BUILD_FORMULA_PACKS.items():
            with self.subTest(formula=name):
                data = load_formula(packs_root / pack_dir, name)
                methodology = data.get("metadata", {}).get("gc", {}).get("methodology")
                self.assertIsNotNone(
                    methodology,
                    f"{name} must declare [metadata.gc.methodology]",
                )
                self.assertEqual(
                    set(methodology),
                    set(METHODOLOGY_METADATA_VOCABULARY),
                )
                self.assertEqual(methodology["implementation_strategy"], "drain")
                self.assertEqual(
                    methodology["allowed_drain_policies"],
                    ["separate", "same-session"],
                )
                self.assertEqual(
                    set(methodology["interaction_modes"]),
                    METHODOLOGY_METADATA_VOCABULARY["interaction_modes"],
                )
                self.assertEqual(
                    set(methodology["review_modes"]),
                    METHODOLOGY_METADATA_VOCABULARY["review_modes"],
                )

    def test_methodology_metadata_uses_only_allowed_vocabulary(self) -> None:
        gascity_root = pathlib.Path(__file__).resolve().parents[1]
        packs_root = gascity_root.parent
        pack_dirs = sorted(set(TOP_LEVEL_BUILD_FORMULA_PACKS.values()))
        declaring = []
        for pack_dir in pack_dirs:
            for path in sorted((packs_root / pack_dir / "formulas").glob("*.formula.toml")):
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                methodology = data.get("metadata", {}).get("gc", {}).get("methodology")
                if methodology is None:
                    continue
                declaring.append((pack_dir, path.name))
                with self.subTest(pack=pack_dir, formula=path.name):
                    unknown_keys = set(methodology) - set(METHODOLOGY_METADATA_VOCABULARY)
                    self.assertFalse(
                        unknown_keys,
                        f"unknown methodology metadata keys: {sorted(unknown_keys)}",
                    )
                    strategy = methodology.get("implementation_strategy")
                    self.assertIn(
                        strategy,
                        METHODOLOGY_METADATA_VOCABULARY["implementation_strategy"],
                    )
                    drain_policies = methodology.get("allowed_drain_policies", [])
                    self.assertLessEqual(
                        set(drain_policies),
                        METHODOLOGY_METADATA_VOCABULARY["allowed_drain_policies"],
                    )
                    if strategy != "convoy-step":
                        self.assertTrue(
                            drain_policies,
                            "allowed_drain_policies may be empty only when "
                            'implementation_strategy = "convoy-step"',
                        )
                    interaction_modes = methodology.get("interaction_modes", [])
                    self.assertTrue(interaction_modes)
                    self.assertLessEqual(
                        set(interaction_modes),
                        METHODOLOGY_METADATA_VOCABULARY["interaction_modes"],
                    )
                    review_modes = methodology.get("review_modes", [])
                    self.assertTrue(review_modes)
                    self.assertLessEqual(
                        set(review_modes),
                        METHODOLOGY_METADATA_VOCABULARY["review_modes"],
                    )
        for name, pack_dir in TOP_LEVEL_BUILD_FORMULA_PACKS.items():
            self.assertIn((pack_dir, f"{name}.formula.toml"), declaring)

    def test_methodology_mode_vars_have_valid_defaults(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        for name, expected_defaults in MODE_VAR_DEFAULTS.items():
            resolved = resolve_formula(root, name)
            text = effective_formula_text(root, name)
            for var_name, default in expected_defaults.items():
                with self.subTest(formula=name, var=var_name):
                    self.assertIn(var_name, resolved["vars"])
                    self.assertEqual(resolved["vars"][var_name]["default"], default)
                    vocabulary = METHODOLOGY_METADATA_VOCABULARY[f"{var_name}s"]
                    if default == "":
                        # Only the issue-fix adapter alias normalization may
                        # leave interaction_mode empty at launch.
                        self.assertEqual(name, "github-issue-fix-base")
                        self.assertEqual(var_name, "interaction_mode")
                    else:
                        self.assertIn(default, vocabulary)
                    self.assertIn(f"{{{{{var_name}}}}}", text)

    def test_github_adapters_validate_methodology_compatibility(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        issue_snapshot = (
            root / "assets/workflows/github-issue-fix-base/snapshot.md"
        ).read_text(encoding="utf-8")
        for fragment in (
            "gc.var.interaction_mode",
            "gc formula show <formula-name> --json",
            "[metadata.gc.methodology]",
            "allowed_drain_policies",
            "interaction_modes",
            "review_modes",
            "convoy-step",
            "gc.github.methodology_compat=blocked",
            "gc.blocked_reason",
            "gc.failure_class=methodology_incompatible",
            "never ask questions",
        ):
            with self.subTest(asset="github-issue-fix-base/snapshot.md", fragment=fragment):
                self.assertIn(fragment, issue_snapshot)
        for selector in METHODOLOGY_FORMULA_VARS:
            with self.subTest(asset="github-issue-fix-base/snapshot.md", selector=selector):
                self.assertIn(f"{{{{{selector}}}}}", issue_snapshot)

        pr_snapshot = (
            root / "assets/workflows/github-pr-review/snapshot.md"
        ).read_text(encoding="utf-8")
        for fragment in (
            "gc formula show {{code_review_formula}} --json",
            "[metadata.gc.methodology]",
            "review_modes",
            "interaction_modes",
            "gc.github.methodology_compat=blocked",
            "gc.blocked_reason",
            "gc.failure_class=methodology_incompatible",
            "headless",
            "human_gate",
        ):
            with self.subTest(asset="github-pr-review/snapshot.md", fragment=fragment):
                self.assertIn(fragment, pr_snapshot)

        pr_run_review = (
            root / "assets/workflows/github-pr-review/run-review.md"
        ).read_text(encoding="utf-8")
        for fragment in (
            '--var interaction_mode="{{interaction_mode}}"',
            '--var review_mode="{{review_mode}}"',
        ):
            with self.subTest(asset="github-pr-review/run-review.md", fragment=fragment):
                self.assertIn(fragment, pr_run_review)

        issue_build = (
            root / "assets/workflows/github-issue-fix-base/build.md"
        ).read_text(encoding="utf-8")
        for fragment in ("gc.var.interaction_mode", "{{review_mode}}"):
            with self.subTest(asset="github-issue-fix-base/build.md", fragment=fragment):
                self.assertIn(fragment, issue_build)

        prepare = (root / "assets/workflows/build-base/prepare.md").read_text(encoding="utf-8")
        for fragment in (
            "[metadata.gc.methodology]",
            "gc.blocked_reason",
            "gc.failure_class=methodology_incompatible",
            "never ask questions",
            "derive the running formula from the claimed step bead's `gc.step_ref`",
            "gc formula show <running-formula> --json",
            "Do not inspect pack source directories",
            ".beads/config.yaml",
            "Close commands do not accept metadata flags",
            "gc bd update <claimed-step-id> --set-metadata 'gc.outcome=pass'",
            "gc bd close <claimed-step-id> --reason",
            "Do not pass `--set-metadata` or `--metadata` to `gc bd close`",
            "do not use\n`gc.outcome=success`",
        ):
            with self.subTest(asset="build-base/prepare.md", fragment=fragment):
                self.assertIn(fragment, prepare)

    def test_build_base_is_full_lifecycle_virtual_contract(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = load_formula(root, "build-base")

        self.assertTrue(data["internal"])
        self.assertTrue(data["target_required"])
        self.assertNotIn("catalog", data)
        self.assertEqual([step["id"] for step in data["steps"]], BUILD_BASE_STEPS)
        self.assertNotIn("compound", BUILD_BASE_STEPS)
        self.assertEqual(data["vars"]["implementation_target"]["default"], "gc.implementation-worker")
        for var_name, default in METHODOLOGY_FORMULA_VARS.items():
            self.assertEqual(data["vars"][var_name]["default"], default)

        route_by_step = {step["id"]: step["metadata"]["gc.run_target"] for step in data["steps"]}
        self.assertEqual(route_by_step["prepare"], "gc.run-operator")
        self.assertEqual(route_by_step["requirements"], "gc.requirements-planner")
        self.assertEqual(route_by_step["plan"], "gc.design-author")
        self.assertEqual(route_by_step["plan-review"], "gc.review-synthesizer")
        self.assertEqual(route_by_step["decompose"], "gc.task-decomposer")
        self.assertEqual(route_by_step["implement"], "{{implementation_target}}")
        self.assertEqual(route_by_step["implement-same-session"], "{{implementation_target}}")
        self.assertEqual(route_by_step["review"], "gc.implementation-reviewer")
        self.assertEqual(route_by_step["finalize"], "gc.run-operator")
        self.assertEqual(route_by_step["publish"], "gc.publisher")

        for step in data["steps"]:
            description = node_description(root, step)
            with self.subTest(step=step["id"]):
                self.assertIn("override", description.lower())
                self.assertIn("build-base", description)

        decompose = next(step for step in data["steps"] if step["id"] == "decompose")
        decompose_description = node_description(root, decompose)
        for fragment in (
            "gc.input_convoy_id",
            "implementation convoy",
            "workflow root bead",
            "before closing",
        ):
            with self.subTest(step="decompose", fragment=fragment):
                self.assertIn(fragment, decompose_description)

        prepare = next(step for step in data["steps"] if step["id"] == "prepare")
        prepare_description = node_description(root, prepare)
        for fragment in (
            "artifact_root: {{artifact_root}}",
            "context_path: {{context_path}}",
            "requirements_path: {{requirements_path}}",
            "plan_path: {{plan_path}}",
            "decomposition_path: {{decomposition_path}}",
            "drain_policy: {{drain_policy}}",
            "interaction_mode: {{interaction_mode}}",
            "review_mode: {{review_mode}}",
            "implementation_target: {{implementation_target}}",
            "planning_formula: {{planning_formula}}",
            "decomposition_formula: {{decomposition_formula}}",
            "implementation_formula: {{implementation_formula}}",
            "implementation_item_formula: {{implementation_item_formula}}",
            "code_review_formula: {{code_review_formula}}",
            "review_fix_formula: {{review_fix_formula}}",
            "max_iterations: {{max_iterations}}",
            "push: {{push}}",
            "open_pr: {{open_pr}}",
            "plain scalar strings",
            "--metadata",
            "--set-metadata 'key=value'",
            "Do not write",
            'values like `"false"` or `"10"`',
        ):
            with self.subTest(step="prepare", fragment=fragment):
                self.assertIn(fragment, prepare_description)

    def test_build_from_decompose_is_suffix_continuation_entrypoint(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = load_formula(root, "build-from-decompose")
        resolved = resolve_formula(root, "build-from-decompose")

        self.assertFalse(data["target_required"])
        self.assertEqual(data["extends"], ["build-from-decompose-base"])
        self.assertEqual(data["catalog"]["name"], "build-from-decompose")
        self.assertEqual({step["id"] for step in resolved["steps"]}, BUILD_FROM_DECOMPOSE_STEPS)
        self.assertNotIn("requirements", BUILD_FROM_DECOMPOSE_STEPS)
        self.assertNotIn("plan", BUILD_FROM_DECOMPOSE_STEPS)
        self.assertNotIn("plan-review", BUILD_FROM_DECOMPOSE_STEPS)

        required_vars = {
            "artifact_root",
            "requirements_path",
            "plan_path",
            "plan_review_path",
        }
        for var_name in required_vars:
            with self.subTest(var=var_name):
                self.assertTrue(resolved["vars"][var_name]["required"])

        expected_defaults = {
            "context_path": "",
            "decomposition_path": "",
            "drain_policy": "separate",
            "interaction_mode": "interactive",
            "review_mode": "agent",
            "implementation_target": "gc.implementation-worker",
            "decomposition_formula": "decomposition-base",
            "implementation_formula": "implement",
            "implementation_item_formula": "do-work-item",
            "code_review_formula": "review",
            "review_fix_formula": "fix-loop-base",
            "max_iterations": "10",
            "push": "false",
            "open_pr": "false",
        }
        for var_name, default in expected_defaults.items():
            with self.subTest(var=var_name):
                self.assertEqual(resolved["vars"][var_name]["default"], default)

        steps = {step["id"]: step for step in resolved["steps"]}
        self.assertEqual(steps["prepare-decompose"]["metadata"]["gc.run_target"], "gc.run-operator")
        self.assertEqual(steps["decompose"]["metadata"]["gc.run_target"], "gc.task-decomposer")
        self.assertEqual(steps["decompose"]["needs"], ["prepare-decompose"])
        self.assertEqual(steps["prepare-convoy"]["needs"], ["decompose"])
        self.assertEqual(steps["implement"]["needs"], ["prepare-convoy"])
        self.assertEqual(steps["implement"]["condition"], "{{drain_policy}} == separate")
        self.assertEqual(steps["implement"]["metadata"]["gc.run_target"], "{{implementation_target}}")
        self.assertEqual(steps["implement"]["drain"]["context"], "separate")
        self.assertEqual(steps["implement"]["drain"]["formula"], "do-work")
        self.assertEqual(steps["implement"]["drain"]["member_access"], "exclusive")
        self.assertEqual(steps["implement-same-session"]["needs"], ["prepare-convoy"])
        self.assertEqual(steps["implement-same-session"]["condition"], "{{drain_policy}} == same-session")
        self.assertEqual(steps["implement-same-session"]["metadata"]["gc.run_target"], "{{implementation_target}}")
        self.assertEqual(steps["implement-same-session"]["drain"]["context"], "shared")
        self.assertEqual(steps["implement-same-session"]["drain"]["formula"], "do-work-item")
        self.assertEqual(steps["implement-same-session"]["drain"]["member_access"], "exclusive")
        self.assertEqual(steps["implement-same-session"]["drain"]["on_item_failure"], "skip_remaining")
        self.assertTrue(steps["implement-same-session"]["drain"]["item"]["single_lane"])
        self.assertEqual(steps["prepare-review"]["needs"], ["implement", "implement-same-session"])
        self.assertEqual(steps["review"]["needs"], ["prepare-review"])
        self.assertEqual(steps["repair-review"]["needs"], ["review"])
        self.assertEqual(steps["finalize"]["needs"], ["repair-review"])
        self.assertEqual(steps["publish"]["needs"], ["finalize"])

        text = effective_formula_text(root, "build-from-decompose")
        for fragment in (
            "continuation entrypoint",
            "requirements_path: {{requirements_path}}",
            "plan_path: {{plan_path}}",
            "plan_review_path: {{plan_review_path}}",
            "gc.input_convoy_id",
            "implementation convoy",
            "Do not rerun requirements, plan, or plan-review",
            "code_review_formula: {{code_review_formula}}",
            "review_fix_formula: {{review_fix_formula}}",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_build_from_decompose_base_is_reusable_suffix_contract(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = load_formula(root, "build-from-decompose-base")
        resolved = resolve_formula(root, "build-from-decompose-base")

        self.assertTrue(data["internal"])
        self.assertFalse(data["target_required"])
        self.assertNotIn("catalog", data)
        self.assertEqual(data["extends"], ["build-from-convoy-base"])
        self.assertEqual({step["id"] for step in resolved["steps"]}, BUILD_FROM_DECOMPOSE_STEPS)

        for var_name in (
            "decomposition_formula",
            "implementation_formula",
            "implementation_item_formula",
            "code_review_formula",
            "review_fix_formula",
        ):
            with self.subTest(var=var_name):
                self.assertIn(var_name, resolved["vars"])

        text = effective_formula_text(root, "build-from-decompose-base")
        for fragment in (
            "continuation entrypoint",
            "concrete methodology packs extend this base",
            "Do not rerun requirements, plan, or plan-review",
            "gc.input_convoy_id",
            "implementation convoy",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_build_continuation_bases_form_nested_suffix_chain(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        expected = {
            "build-from-review-base": {
                "extends": None,
                "steps": BUILD_FROM_REVIEW_STEPS,
            },
            "build-from-convoy-base": {
                "extends": ["build-from-review-base"],
                "steps": BUILD_FROM_CONVOY_STEPS,
            },
            "build-from-decompose-base": {
                "extends": ["build-from-convoy-base"],
                "steps": BUILD_FROM_DECOMPOSE_STEPS,
            },
            "build-from-plan-base": {
                "extends": ["build-from-decompose-base"],
                "steps": BUILD_FROM_PLAN_STEPS,
            },
            "build-from-requirements-base": {
                "extends": ["build-from-plan-base"],
                "steps": BUILD_FROM_REQUIREMENTS_STEPS,
            },
        }
        for formula, spec in expected.items():
            with self.subTest(formula=formula):
                data = load_formula(root, formula)
                resolved = resolve_formula(root, formula)
                self.assertTrue(data["internal"])
                self.assertFalse(data["target_required"])
                self.assertNotIn("catalog", data)
                if spec["extends"] is None:
                    self.assertNotIn("extends", data)
                else:
                    self.assertEqual(data["extends"], spec["extends"])
                self.assertEqual({step["id"] for step in resolved["steps"]}, spec["steps"])

        chain = resolve_formula(root, "build-from-requirements-base")
        steps = {step["id"]: step for step in chain["steps"]}
        self.assertEqual(steps["requirements"]["needs"], ["prepare-requirements"])
        self.assertEqual(steps["prepare-plan"]["needs"], ["requirements"])
        self.assertEqual(steps["plan"]["needs"], ["prepare-plan"])
        self.assertEqual(steps["plan-review"]["needs"], ["plan"])
        self.assertEqual(steps["prepare-decompose"]["needs"], ["plan-review"])
        self.assertEqual(steps["decompose"]["needs"], ["prepare-decompose"])
        self.assertEqual(steps["prepare-convoy"]["needs"], ["decompose"])
        self.assertEqual(steps["implement"]["needs"], ["prepare-convoy"])
        self.assertEqual(steps["implement-same-session"]["needs"], ["prepare-convoy"])
        self.assertEqual(steps["prepare-review"]["needs"], ["implement", "implement-same-session"])
        self.assertEqual(steps["review"]["needs"], ["prepare-review"])
        self.assertEqual(steps["repair-review"]["needs"], ["review"])
        self.assertEqual(steps["finalize"]["needs"], ["repair-review"])
        self.assertEqual(steps["publish"]["needs"], ["finalize"])

    def test_build_from_review_blocked_results_are_healable_not_passed(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        resolved = resolve_formula(root, "build-from-review-base")
        steps = {step["id"]: step for step in resolved["steps"]}

        self.assertEqual(steps["repair-review"]["metadata"]["gc.run_target"], "gc.run-operator")
        self.assertEqual(steps["repair-review"]["description_file"], "../assets/workflows/build-from-review-base/repair-review.md")

        text = effective_formula_text(root, "build-from-review-base")
        for fragment in (
            "review_mode=report",
            "gc.build.repair_status",
            "gc.restart.entrypoint",
            "gc.restart.reason",
            "gc.outcome=fail",
            "Do not close the workflow root with `gc.outcome=pass`",
            "Publishing disabled or no-op status must never convert",
            # gc-0v22om: the engine derives the whole workflow's terminal
            # outcome from the graph SINK step's own claimed-bead outcome
            # (publish, for this formula) -- not from the workflow root's
            # metadata. Root-only instructions let a worker record a
            # correct "blocked" root while still closing its own claimed
            # step (finalize/repair-review/publish) with gc.outcome=pass,
            # because "the step ran without an internal error" and "the
            # reviewed work was approved" are different things. Confirmed
            # against two real incidents (gcas-4iopbg, gcas-vezqmb) where
            # exactly this happened -- see gc-0v22om.
            "close THIS step's own claimed bead",
            "with the same outcome",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_repair_review_terminal_state_follows_final_verdict(self) -> None:
        # gc-jxl5x: workflow root gcas-gigyfp closed gc.outcome=fail /
        # gc.failure_class=review_repair_blocked even though its own final
        # re-review artifact read verdict=approved with 0 unresolved
        # findings (the work had already merged as PR #2412). The repair
        # loop must derive its terminal state from the LAST re-review
        # artifact's own verdict, not from whether the attempt counter
        # reached max_iterations, and finalize must independently re-check
        # that artifact before trusting a failing repair_status.
        root = pathlib.Path(__file__).resolve().parents[1]
        text = effective_formula_text(root, "build-from-review-base")
        for fragment in (
            "determined by re-reading",
            "MOST RECENT re-review artifact's own verdict",
            "attempt counter reached max_iterations",
            "also refresh `gc.build.review_verdict`",
            "gc.failure_class=review_repair_exhausted",
            "`review_repair_blocked` means a prerequisite\nwas missing, nothing else",
            "independently re-open the",
            "gc.failure_class=repair_status_contradicts_review",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_publish_resolves_approved_commit_from_latest_review_only(self) -> None:
        # gc-ajt3i: continuation root gcas-p0g3rr's publish step closed with
        # "branch was already pushed at approved commit 00c89571d" -- the
        # PREVIOUS blocked run's pre-repair head -- while the real approved
        # commit from the final re-review (88bc1992f, three repair commits
        # later) was never pushed. PR #2462 opened on unreviewed-stale code.
        # The review/repair-review stages must record a single canonical
        # `gc.build.review_subject_commit` fresh at approval time, finalize
        # must not disturb it, and publish must resolve the approved commit
        # from it alone, checked against the actual remote ref -- never from
        # carried-over publish/implementation-summary metadata.
        root = pathlib.Path(__file__).resolve().parents[1]
        text = effective_formula_text(root, "build-from-review-base")
        for fragment in (
            "record `gc.build.review_subject_commit`",
            "resolved fresh right now from the actual reviewed\nworktree's `git rev-parse HEAD`",
            "never copied from `implementation_summary_path`",
            "also refresh `gc.build.review_verdict`,\n`gc.build.review_report_path`, and `gc.build.review_subject_commit`",
            "Do not blank, reset, or\notherwise touch `gc.build.review_subject_commit`",
            "Read `gc.build.review_subject_commit` on the workflow root. This is the\n   ONLY source of truth for the approved commit",
            "git ls-remote origin",
            "fast-forward push `gc.build.review_subject_commit` to",
            "gc.build.publish_pushed_commit",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_implementation_steps_open_only_a_draft_pr_and_never_mark_it_ready(self) -> None:
        # gc-5gm0d: a shared-drain implementation worker opened a ready
        # fernanja/ascent_app#2459 while build-from-plan's own
        # implement/review/repair-review/publish steps were all still open;
        # an automated merge sweep could have squash-merged it the moment CI
        # went green, and only a human converting it to draft prevented that.
        # gc-68exu then needed CI to run before review, which needs a pull
        # request: the implementation step opens it as a DRAFT (the sweep
        # skips drafts), and marking it ready stays with the publish step.
        root = pathlib.Path(__file__).resolve().parents[1]
        for formula in ("implementation-base", "do-work", "implementation-item-base", "do-work-item"):
            with self.subTest(formula=formula):
                text = effective_formula_text(root, formula)
                for fragment in (
                    "`gh pr create --draft --base <default-branch> --head <branch>",
                    "with the source bead id in the title",
                    "Never open it\n   non-draft and never mark it ready",
                    "gc-5gm0d",
                    "Marking it ready belongs exclusively to the publish step",
                    "When push or open_pr is not `true`, do not open a pull request",
                ):
                    self.assertIn(fragment, text)
                # The only `gh pr ready` an implementation step is told about
                # is the one that makes a ready pull request a draft again.
                self.assertIn("`gh pr ready --undo <number>` makes", text)
                self.assertEqual(text.count("gh pr ready"), text.count("gh pr ready --undo"))
                self.assertNotIn("draft or otherwise", text)

    def test_handoff_steps_are_gated_on_green_ci(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]

        for (formula_name, step_id), (script, doc) in CI_GREEN_HANDOFF_STEPS.items():
            with self.subTest(formula=formula_name, step=step_id):
                formula = load_formula(root, formula_name)
                step = {node["id"]: node for node in formula["steps"]}[step_id]
                self.assertEqual(step["check"]["max_attempts"], 3)
                self.assertEqual(
                    step["check"]["check"],
                    {"mode": "exec", "path": f"../assets/scripts/checks/{script}", "timeout": "5m"},
                )
                for var_name in ("push", "open_pr"):
                    self.assertEqual(formula["vars"][var_name]["default"], "false")
                # Compare on one line: these paragraphs are re-wrapped per doc.
                text = " ".join((root / "assets" / "workflows" / doc).read_text(encoding="utf-8").split())
                for fragment in (
                    "gc-68exu",
                    "push {{push}} and open_pr {{open_pr}}",
                    # The wait is bounded, and a stuck check has a way out.
                    BOUNDED_CI_WAIT_COMMAND,
                    "the wrapper ends it after 25 minutes (status 124)",
                    "a bare `alarm` before `exec` does not stop `gh`",
                    "Do not replace it with a fixed sleep or a long poll interval",
                    "A check that is running gets another bounded wait, three waits at most.",
                    "is stuck: do not wait again",
                    "you may rerun the failed job once (`gh run rerun <run-id> --failed`) and must record",
                    "The gate does not tell a flake from a defect: a check that is still red is red.",
                    MANUAL_PR_CI_GREEN_COMMAND,
                    "../assets/scripts/checks/pr-ci-green.sh",
                    "controller does not wait for CI.",
                    "skipped: no publishing intent",
                    # The worker fixes what its branch broke, and never by
                    # weakening a test.
                    "A failing test is fixed; it is never skipped, quarantined or re-baselined away.",
                    # A failure the base branch also has is reported with the
                    # evidence: not fixed on this branch, not ignored.
                    "when the same check is red on the base branch's own most recent run of it, "
                    "and the base branch does not require that check, the gate lets it pass and prints a `WARNING` line naming both runs",
                    "Do not fix the base branch's failure on this branch, and do not pass over it in silence",
                    "copy that `WARNING` line, with both run URLs, into",
                    "The gate makes this call, not you: a red check that the base branch requires, "
                    "that is green on the base branch, or that the base branch has never run, blocks.",
                    # The honest exit when CI cannot be made green.
                    "If CI cannot be made green:",
                    "do not weaken, skip or re-baseline a test",
                    HARD_FAILURE_CLOSE_COMMAND,
                    "`gc.failure_class=hard` ends the step at once",
                    "a failed outcome without it is handed back as a new attempt, up to three times",
                    # A ready pull request fails the gate; infrastructure does
                    # not count as an attempt.
                    "marked ready instead of draft (`gh pr ready --undo <number>` makes it a draft again)",
                    "GitHub or the bead store being unreachable is not an attempt",
                ):
                    self.assertIn(fragment, text, f"{doc} lost: {fragment}")
                self.assertNotIn("that includes a test that was already failing before", text)
                self.assertNotIn("alarm shift; exec @ARGV", text)

        # The wrapper really chains both gates, artifact first.
        wrapper = (root / "assets" / "scripts" / "checks" / "implementation-handoff-valid.sh").read_text(
            encoding="utf-8"
        )
        # Exit codes pass through (75 is "no verdict", not a failed attempt).
        self.assertLess(
            wrapper.index('"$SCRIPT_DIR/build-artifact-valid.sh" || exit $?'),
            wrapper.index('exec "$SCRIPT_DIR/pr-ci-green.sh"'),
        )
        self.assertNotIn("|| exit 1", wrapper.split("SCRIPT_DIR=", 1)[1].split("done", 1)[1])

    def test_review_family_reaches_every_review_through_a_gated_handoff(self) -> None:
        # build-from-plan / -decompose / -convoy: the first review follows the
        # implement drain, whose items run do-work (separate sessions) or
        # do-work-item (one shared session); every later review is the fix
        # loop's re-review, which follows apply-fixes.
        root = pathlib.Path(__file__).resolve().parents[1]
        for formula_name in ("build-from-plan", "build-from-decompose", "build-from-convoy"):
            with self.subTest(formula=formula_name):
                formula = resolve_formula(root, formula_name)
                steps = {step["id"]: step for step in formula["steps"]}
                self.assertEqual(steps["implement"]["drain"]["formula"], "do-work")
                self.assertEqual(steps["implement-same-session"]["drain"]["formula"], "do-work-item")
                self.assertEqual(steps["prepare-review"]["needs"], ["implement", "implement-same-session"])
                self.assertEqual(steps["review"]["needs"], ["prepare-review"])
                self.assertEqual(formula["vars"]["review_fix_formula"]["default"], "fix-loop-base")
                for var_name in ("push", "open_pr"):
                    self.assertIn(var_name, formula["vars"])

        fix_loop = load_formula(root, "fix-loop-base")
        steps = {step["id"]: step for step in fix_loop["steps"]}
        self.assertEqual(steps["re-review"]["needs"], ["apply-fixes"])
        self.assertEqual(
            pathlib.PurePosixPath(steps["apply-fixes"]["check"]["check"]["path"]).name, "pr-ci-green.sh"
        )

        apply_fixes = (root / "assets/workflows/fix-loop-base/apply-fixes.md").read_text(encoding="utf-8")
        for fragment in (
            "or whenever the branch under repair already has an open pull\nrequest",
            '--set-metadata "gc.build.handoff_commit=$(git rev-parse HEAD)" --set-metadata "gc.build.handoff_branch=<branch>"',
            "Leave a draft a draft: marking it ready belongs exclusively to the\n   publish step.",
            "the re-review does not start",
            "run `git checkout --detach` in your own directory",
            # A worktree with someone else's uncommitted work ends the step in
            # one attempt; a plain failed outcome would be re-dispatched three
            # times into the same worktree.
            "Do not touch it. Close this step as a hard failure and name the path:",
            '--set-metadata gc.failure_class=hard --set-metadata "gc.failure_reason=uncommitted work on <branch> in <path>"',
            "Without `gc.failure_class=hard` the\n  step is handed back as a new attempt, up to three times",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, apply_fixes)
        self.assertNotIn("Close this step with a failed outcome", apply_fixes)

        # The build passes its publishing intent into the loop it launches.
        for formula_name in ("build-from-plan", "build-from-review"):
            repair = rendered_step_text(root, formula_name, "repair-review")
            with self.subTest(formula=formula_name, step="repair-review"):
                self.assertIn('--var "push={{push}}" \\\n     --var "open_pr={{open_pr}}"', repair)

        # Reviewers still read CI themselves and review the commit CI ran on.
        for doc in ("build-from-review-base/review.md", "fix-loop-base/re-review.md"):
            text = (root / "assets" / "workflows" / doc).read_text(encoding="utf-8")
            with self.subTest(doc=doc):
                self.assertIn("Start with the automated checks, before reading any code.", text)
                self.assertIn("a gate that recorded `skipped` proves nothing", text)
                self.assertIn("Review\nthe commit at the pull request head.", text)

    def test_publish_marks_the_existing_draft_ready_instead_of_opening_a_second_pr(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        for formula_name in ("build-from-plan", "build-from-decompose", "build-from-review"):
            text = rendered_step_text(root, formula_name, "publish")
            for fragment in (
                # Every safety rule the publish step already had.
                "Read `gc.build.review_subject_commit` on the workflow root. This is the\n   ONLY source of truth for the approved commit",
                "git ls-remote origin",
                "fast-forward push `gc.build.review_subject_commit` to",
                "do NOT open a PR, do NOT report `not_published`/`noop`",
                "gc.build.publish_pushed_commit",
                "never skip or bypass a hook to\nget the push through",
                # The draft opened by the implementation stage.
                "The pull request usually exists already, as a draft (gc-68exu).",
                "`gh pr list --head <branch> --state open --json number,isDraft,headRefOid,url`",
                "One open pull request: do not open a second.",
                "Confirm its `headRefOid` equals\n  `gc.build.review_subject_commit`",
                "`gh pr ready <number>`",
                "No open pull request: open one, as before.",
                "More than one: fail closed and name them",
                "leave an existing draft exactly as it is: do not mark it\n  ready and do not close it",
            ):
                with self.subTest(formula=formula_name, fragment=fragment):
                    self.assertIn(fragment, text)
            self.assertLess(
                text.index("Only after the remote branch tip is confirmed to equal"),
                text.index("The pull request usually exists already, as a draft"),
            )
        for doc in ("implement/publish.md", "build-basic/publish.md"):
            text = (root / "assets" / "workflows" / doc).read_text(encoding="utf-8")
            with self.subTest(doc=doc):
                self.assertIn("gc-68exu", text)
                self.assertIn("`gh pr ready <number>`", text)

    # gc-50j4j (R13 retro): build-from-plan and build-basic have no shared
    # plan/plan-review/decompose/repair file (each overrides its own), so the
    # same prose lives in each rendered copy; implementation prose is shared
    # through the do-work / do-work-item drain formulas.
    R13_BUILD_FORMULAS = ("build-from-plan", "build-basic", "build-from-decompose")

    def test_plan_traces_consumer_call_paths(self) -> None:
        # R13 Part 1 added FilterBar `wrap` and tested FilterBar alone; the
        # Reports page reached it through ListTemplate.Filters, which never
        # forwarded `wrap`. The plan must trace the consumer call path and
        # test at the consumer-facing layer.
        root = pathlib.Path(__file__).resolve().parents[1]
        for formula in ("build-from-plan", "build-basic", "build-from-requirements"):
            text = rendered_step_text(root, formula, "plan")
            for fragment in (
                "Trace consumer call paths",
                "reference -> page/consumer -> wrapper -> primitive",
                "at the consumer-facing layer, not only on the primitive",
                "list each dependency on an earlier part's\ndeliverable with its concrete call path",
            ):
                with self.subTest(formula=formula, fragment=fragment):
                    self.assertIn(fragment, text)

    def test_plan_review_blocks_scope_conflicts_and_carries_notes(self) -> None:
        # R13 Part 2 banned packages/ui edits, making an AC unreachable;
        # plan-review approved and review found it hours later. Accurate
        # plan-review notes were ignored because nothing carried them forward.
        root = pathlib.Path(__file__).resolve().parents[1]
        for formula in ("build-from-plan", "build-basic", "build-from-requirements"):
            text = rendered_step_text(root, formula, "plan-review")
            for fragment in (
                "Check scope reachability",
                "If a scope ban makes a criterion\nunreachable, record a blocking finding",
                "Never approve around the conflict.",
                "`advisory` or `must-address`",
                "`## Must-Address Notes`",
            ):
                with self.subTest(formula=formula, step="plan-review", fragment=fragment):
                    self.assertIn(fragment, text)
        for formula in self.R13_BUILD_FORMULAS:
            text = rendered_step_text(root, formula, "decompose")
            for fragment in (
                "Carry plan-review must-address notes forward",
                "`## Must-Address Notes`",
                "unchecked checklist",
            ):
                with self.subTest(formula=formula, step="decompose", fragment=fragment):
                    self.assertIn(fragment, text)
        mayor = (root / "skills" / "mayor" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("do not write a flat ban", mayor)
        self.assertIn("sanctioned exception", mayor)

    def test_plan_names_how_each_criterion_is_observed(self) -> None:
        # R13 Part 1 lost 2 review rounds: headless Chromium never draws
        # classic scrollbars, so a scrollbar-gutter AC could only be
        # modelled, and headed Vitest was broken. The plan must name an
        # observation method the environment can really use (or the real
        # alternative), and plan-review must block when it does not.
        root = pathlib.Path(__file__).resolve().parents[1]
        for formula in ("build-from-plan", "build-basic", "build-from-requirements"):
            plan = rendered_step_text(root, formula, "plan")
            for fragment in (
                "Name how each acceptance criterion is observed",
                "confirm the test environment can actually\nobserve it",
                "headless Chromium never draws classic scrollbars",
                "a documented manual check with evidence",
                "Never fall back silently to\nmodelling, and never skip verification.",
            ):
                with self.subTest(formula=formula, step="plan", fragment=fragment):
                    self.assertIn(fragment, plan)
            review = rendered_step_text(root, formula, "plan-review")
            for fragment in (
                "acceptance criterion with no observation method",
                "cannot observe with no stated alternative verification, as a\nblocking finding",
            ):
                with self.subTest(formula=formula, step="plan-review", fragment=fragment):
                    self.assertIn(fragment, review)

    def test_implementation_and_repair_run_self_check_before_review(self) -> None:
        # R13: 11 of 15 repair loops were implementation errors a self-check
        # catches (unreachable cited SHAs, metadata/text mismatches, skeleton
        # baselines, unrun gates). The check fails closed in the step itself.
        root = pathlib.Path(__file__).resolve().parents[1]
        for formula in self.R13_BUILD_FORMULAS:
            for step in ("implement", "implement-same-session"):
                text = drained_formula_text(root, formula, step)
                for fragment in (
                    "`### Self-Check`",
                    "It fails closed",
                    "It must never be left for review to find.",
                    "`git merge-base --is-ancestor <sha> HEAD`; re-cite after any rebase",
                    "Summary metadata (coverage numbers, file counts, verdicts) matches",
                    "Regenerated visual baselines/snapshots were opened and checked",
                    "Every required gate the plan names",
                    "must-address checklist item",
                ):
                    with self.subTest(formula=formula, step=step, fragment=fragment):
                        self.assertIn(fragment, text)
            text = repair_step_text(root, formula)
            for fragment in (
                "implementation self-check",
                "It fails closed",
                "`git merge-base --is-ancestor <sha> HEAD`",
                "regenerated baselines/snapshots were opened and show the intended",
                "required gates (e.g. preflight) were run with exit codes recorded",
            ):
                with self.subTest(formula=formula, step="repair", fragment=fragment):
                    self.assertIn(fragment, text)

    def test_wording_only_findings_fixed_inside_current_loop(self) -> None:
        # reports-test-hardening: 3 of 6 fix loops only reworded a doc; R13
        # Part 2 stale reply drafts took extra rounds. Prose-only findings are
        # fixed and self-validated in the repair pass, never at the cost of
        # skipping a real re-review for code/test changes.
        root = pathlib.Path(__file__).resolve().parents[1]
        for formula in self.R13_BUILD_FORMULAS:
            text = repair_step_text(root, formula)
            for fragment in (
                "Wording-only findings touch only prose",
                "change no code or test behaviour",
                "Fix them in the same",
                "instead of starting another",
                "re-read each result against its finding",
                "this rule never skips",
            ):
                with self.subTest(formula=formula, fragment=fragment):
                    self.assertIn(fragment, text)
        # build-from-review-base: the in-loop wording commit must still end
        # up as the approved, pushed commit (gc-ajt3i) and be read as the
        # latest review verdict (gc-jxl5x).
        for formula in ("build-from-plan", "build-from-decompose"):
            text = repair_step_text(root, formula)
            for fragment in (
                "wording-fix validation artifact\nwith `status: approved`",
                "is wording-only. It counts as the most recent\nre-review artifact",
                "`gc.build.review_subject_commit`, so publish\npushes the final HEAD",
                "A diff that changes code or test behaviour always gets\na real re-review",
            ):
                with self.subTest(formula=formula, fragment=fragment):
                    self.assertIn(fragment, text)
        basic = repair_step_text(root, "build-basic")
        self.assertIn("or under the\nwording-only rule above", basic)
        self.assertIn("always sets `iterate`", basic)

    # gc-o2ocz (flake retro, gcas-9bowgu): a follow-up bundled a bead fixed
    # 5h earlier; the decomposer noted it as a non-blocking ambiguity and six
    # fix loops chased a flake nobody could reproduce. Fragments are matched
    # on whitespace-flattened prose so re-wrapping a line does not fail them.
    @staticmethod
    def _flat(text: str) -> str:
        return " ".join(text.split())

    def test_requirements_recheck_bundled_sources(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        for formula in ("build-basic", "build-from-requirements"):
            text = self._flat(rendered_step_text(root, formula, "requirements"))
            for fragment in (
                "Re-check bundled sources first.",
                "run `gc bd show <id>` (status, close reason)",
                "read the cited lines on the current base branch",
                "is not a live requirement: drop it citing that evidence",
            ):
                with self.subTest(formula=formula, fragment=fragment):
                    self.assertIn(fragment, text)

    def test_stale_bundled_items_block_plan_and_decompose(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        fragments = (
            "Stale bundled items are blocking.",
            "`gc bd show <id>`, and the cited lines on the current base branch",
            "is a blocking finding, never a non-blocking note",
            "drop it citing that evidence, or set `status: blocked` and stop for a decision",
            'Never relabel an earlier fix "partial" without evidence that it failed.',
        )
        cases = [(f, "plan") for f in ("build-from-plan", "build-basic", "build-from-requirements")]
        cases += [(f, "decompose") for f in (*self.R13_BUILD_FORMULAS, "build-from-requirements")]
        for formula, step in cases:
            text = self._flat(rendered_step_text(root, formula, step))
            for fragment in fragments:
                with self.subTest(formula=formula, step=step, fragment=fragment):
                    self.assertIn(fragment, text)

    def test_timing_fixes_need_reproduction_evidence(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        rule = "the pre-fix code fails and the post-fix code passes under the same reproduction method"
        reviews = {
            "build-from-plan": rendered_step_text(root, "build-from-plan", "review"),
            "build-from-decompose": rendered_step_text(root, "build-from-decompose", "review"),
            "build-basic": expansion_node_text(root, "build-basic", "review", ".test-evidence-review"),
        }
        for formula, raw in reviews.items():
            text = self._flat(raw)
            for fragment in (rule, "the repo's AGENTS.md names it", "`cannot_reproduce`",
                             "Never demand evidence the repo cannot produce."):
                with self.subTest(formula=formula, step="review", fragment=fragment):
                    self.assertIn(fragment, text)
        facts = "Verify load-bearing facts before any wording concern: the SHAs, what actually failed, and the failing code's content at the failure SHA"
        for formula, raw in (
            ("build-from-plan", reviews["build-from-plan"]),
            ("build-from-decompose", reviews["build-from-decompose"]),
            ("build-basic", expansion_node_text(root, "build-basic", "review", ".synthesize-review")),
        ):
            with self.subTest(formula=formula, fragment="load-bearing facts"):
                self.assertIn(facts, self._flat(raw))
        for formula in ("build-from-plan", "build-from-decompose"):
            text = self._flat(reviews[formula])
            self.assertIn("the verdict is `blocked` with reason `cannot_reproduce`", text)
            self.assertIn("never `approved`, never a skip", text)
        for formula in self.R13_BUILD_FORMULAS:
            text = self._flat(repair_step_text(root, formula))
            for fragment in ("under the same reproduction method", "do not guess-fix", "`cannot_reproduce`"):
                with self.subTest(formula=formula, step="repair", fragment=fragment):
                    self.assertIn(fragment, text)
            # cannot_reproduce is a reason, never a new failure_class.
            self.assertNotIn("gc.failure_class=cannot_reproduce", text)
        for formula in ("build-from-plan", "build-from-decompose"):
            text = self._flat(repair_step_text(root, formula))
            for fragment in (
                "`gc.build.repair_status=blocked`",
                "`gc.failure_class=review_repair_blocked`",
                "`gc.restart.reason=cannot_reproduce`",
            ):
                with self.subTest(formula=formula, step="repair", fragment=fragment):
                    self.assertIn(fragment, text)
        basic = self._flat(repair_step_text(root, "build-basic"))
        for fragment in (
            "`gc.failure_class=hard`",
            "`gc.failure_reason=cannot_reproduce`",
            "Except for the `cannot_reproduce` stop above, always close with `gc.outcome=pass`",
        ):
            with self.subTest(formula="build-basic", step="repair", fragment=fragment):
                self.assertIn(fragment, basic)

    def test_preflight_review_instruction_stays_synchronized(self) -> None:
        # build-base and build-from-review-base are separate root formulas
        # (see GC-METH-016 / GC-METH-001) that each carry their own copy of
        # this instruction in their own review.md, because formula step
        # description_file content only supports flat {varname} substitution
        # (internal/formula: substituteVars/substituteExpandedDescription) --
        # there is no {{define}}/{{template}} include mechanism for step
        # markdown the way gc-role-worker.template.md has for role prompts.
        # Real DRY (one file, included twice) isn't available without a
        # framework change; this test is the enforced substitute -- it fails
        # loudly the moment one copy is edited without the other, instead of
        # silently drifting the way it did before 2026-08-23.
        root = pathlib.Path(__file__).resolve().parents[1]
        preflight_instruction_core = (
            "As part of this review, actually run the rig's full local-CI-equivalent gate\n"
            "yourself in the implementation worktree (not the launcher checkout) — `make\n"
            "preflight-fast` if the worktree's Makefile defines that target, otherwise\n"
            "`make preflight` — and record the exact command and its outcome. Do not\n"
            "accept or forward a prose claim about preflight from the implementation stage\n"
            "as a substitute for running it here: that was tried and verified live to be\n"
            "attention-dependent, not guaranteed (one re-review caught a missing run, an\n"
            "identical re-review of a different item did not)."
        )
        for formula in ("build-base", "build-from-review-base"):
            with self.subTest(formula=formula):
                text = effective_formula_text(root, formula)
                self.assertIn(preflight_instruction_core, text)

    def test_default_continuation_entrypoints_extend_suffix_bases(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        expected = {
            "build-from-requirements": "build-from-requirements-base",
            "build-from-plan": "build-from-plan-base",
            "build-from-decompose": "build-from-decompose-base",
            "build-from-convoy": "build-from-convoy-base",
            "build-from-review": "build-from-review-base",
        }
        for formula, base in expected.items():
            with self.subTest(formula=formula):
                data = load_formula(root, formula)
                self.assertEqual(data["extends"], [base])
                self.assertFalse(data["target_required"])
                self.assertEqual(data["catalog"]["name"], formula)
                self.assertNotIn("internal", data)

    def test_build_basic_extends_full_lifecycle_base(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = load_formula(root, "build-basic")
        resolved = resolve_formula(root, "build-basic")

        self.assertEqual(data["extends"], ["build-base"])
        self.assertEqual([step["id"] for step in resolved["steps"]], BUILD_BASE_STEPS)
        self.assertEqual(data["catalog"]["name"], "build-basic")
        review_step = next(step for step in data["steps"] if step["id"] == "review")
        self.assertEqual(review_step["expand"], "build-basic-review")
        self.assertEqual(
            review_step["expand_vars"],
            {
                "implementation_target": "{{implementation_target}}",
            },
        )
        self.assertEqual(review_step["needs"], ["summarize-implementation"])
        self.assertNotIn("check", review_step)

        summary_step = next(step for step in resolved["steps"] if step["id"] == "summarize-implementation")
        self.assertEqual(summary_step["metadata"]["gc.run_target"], "gc.run-operator")
        self.assertEqual(
            summary_step["metadata"]["gc.build.artifact_schema"],
            "gc.build.implementation-summary.v1",
        )
        self.assertEqual(
            summary_step["metadata"]["gc.build.artifact_path_keys"],
            "gc.build.implementation_summary_path",
        )
        self.assertEqual(summary_step["needs"], ["implement", "implement-same-session"])
        text = effective_formula_text(root, "build-basic")
        for fragment in (
            "generate-requirements",
            "implementation-plan",
            "design-review",
            "create-beads",
            "implementation summary path",
            "guided starter factory",
            "factory-run.md",
            "summarize-implementation",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)
        self.assertNotIn('id = "compound"', text)

        decompose = next(step for step in data["steps"] if step["id"] == "decompose")
        decompose_description = node_description(root, decompose)
        for fragment in (
            "gc.input_convoy_id",
            "implementation convoy",
            "workflow root bead",
            "before closing",
            "gc convoy create <name> <work-item-id...> --json",
            "Do not create an empty convoy",
            "Do not call `gc convoy add` for newly-created beads",
            "Do not call `gc bd show <implementation-convoy-id>`",
            "Do not use `gc bd create --root-bead`",
        ):
            with self.subTest(step="decompose", fragment=fragment):
                self.assertIn(fragment, decompose_description)

    def test_build_basic_v2_uses_approachable_factory_techniques(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        review = load_formula(root, "build-basic-review")
        self.assertEqual(review["type"], "expansion")
        self.assertEqual(review["requires"]["formula_compiler"], ">=2.0.0")
        self.assertEqual(
            review["vars"]["implementation_target"]["default"],
            "gc.implementation-worker",
        )

        templates = {template["id"]: template for template in review["template"]}
        loop = templates["{target}.build-basic-review-loop"]
        self.assertEqual(
            [child["id"] for child in loop["children"]],
            [
                "{target}.acceptance-review",
                "{target}.test-evidence-review",
                "{target}.simplicity-review",
                "{target}.preflight-review",
                "{target}.synthesize-review",
                "{target}.apply-review-findings",
            ],
        )
        for target in (
            "gc.implementation-reviewer",
            "gc.gap-analyst",
            "gc.design-implementation-reviewer",
        ):
            with self.subTest(target=target):
                self.assertIn(
                    target,
                    [
                        child["metadata"]["gc.run_target"]
                        for child in loop["children"]
                        if child.get("metadata", {}).get("gc.run_target")
                    ],
                )
        self.assertEqual(
            loop["children"][-1]["metadata"]["gc.continuation_group"],
            "build-basic-review-fixes",
        )

        asset_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((root / "assets" / "workflows" / "build-basic-review").glob("*.md"))
        )
        for fragment in (
            "starter factory",
            "three review lanes",
            "code_review.verdict=done|iterate",
            "code_review.acceptance_verdict=approve",
            "code_review.test_evidence_verdict=approve",
            "code_review.simplicity_verdict=approve",
            "gc bd update \"$CLAIMED_BEAD_ID\"",
            "source anchor/worktree",
            "launcher rig root may remain unchanged",
            "not to the launcher rig root",
            "normalized `gc.build.review.v1` artifact with `status: approved`",
            "Do not invoke provider-native subagents",
            "Implementation Worktrees",
            "`gc.work_dir` is the launcher rig root, not the implementation worktree",
            "Do not inspect or edit the launcher checkout",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, asset_text)

        requirements_text = (root / "assets/workflows/build-basic/requirements.md").read_text(
            encoding="utf-8"
        )
        for fragment in ("goal", "constraints", "acceptance criteria", "non-goals", "open questions"):
            with self.subTest(asset="requirements", fragment=fragment):
                self.assertIn(fragment, requirements_text)

        plan_review_text = (root / "assets/workflows/build-basic/plan-review.md").read_text(
            encoding="utf-8"
        )
        for fragment in (
            "implementation readiness",
            "requirements traceability",
            "task boundaries",
            "test commands",
            "risk",
            "gc.build.plan_review_report_path",
            "Do not write or overwrite\n`gc.build.review_report_path`",
        ):
            with self.subTest(asset="plan-review", fragment=fragment):
                self.assertIn(fragment, plan_review_text)

        for relative_path in (
            "assets/workflows/do-work/implement.md",
            "assets/workflows/do-work-item/implement-item.md",
        ):
            text = (root / relative_path).read_text(encoding="utf-8")
            for fragment in (
                "`## Summary`",
                "`## Intended Behavior`",
                "`## Changed Files`",
                "`## Verification`",
                "`## Remaining Risks`",
                "first verification command",
                "final proof command",
                "observed pass/fail result",
            ):
                with self.subTest(asset=relative_path, fragment=fragment):
                    self.assertIn(fragment, text)

        finalize_text = (root / "assets/workflows/build-basic/finalize.md").read_text(encoding="utf-8")
        publish_text = (root / "assets/workflows/build-basic/publish.md").read_text(encoding="utf-8")
        summary_text = (root / "assets/workflows/build-base/summarize-implementation.md").read_text(encoding="utf-8")
        for fragment in (
            "factory-run.md",
            "methodology",
            "review lanes",
            "next human action",
            "canonical implementation summary",
            "`gc.build.implementation_summary_path`",
            "`implementation-summary.md`",
            "`gc.build.implementation-summary.v1`",
            "source anchor/worktree",
            "not a partial build",
            "Use `status: approved`",
        ):
            with self.subTest(asset="finalize", fragment=fragment):
                self.assertIn(fragment, finalize_text)
        for fragment in (
            "approved source anchor/worktree",
            "Do not mark publish failed or downgrade the workflow",
            "preserving the approved build outcome",
            "Never set\n`gc.outcome=noop`",
            "--set-metadata 'gc.outcome=pass'",
            "--set-metadata 'gc.publish_outcome=noop'",
            "--set-metadata 'gc.publish_mode=disabled'",
        ):
            with self.subTest(asset="publish", fragment=fragment):
                self.assertIn(fragment, publish_text)
        for fragment in (
            "canonical build implementation summary",
            "`gc.build.implementation_summary_path`",
            "`implementation-summary.md`",
            "`gc.build.implementation-summary.v1`",
            "accepted requirement IDs",
            "source anchor ids",
            "per-item summary paths",
        ):
            with self.subTest(asset="summarize-implementation", fragment=fragment):
                self.assertIn(fragment, summary_text)

        for relative_path in (
            "assets/workflows/build-basic/requirements.md",
            "assets/workflows/build-basic/plan.md",
            "assets/workflows/build-basic/decompose.md",
            "assets/workflows/build-base/summarize-implementation.md",
            "assets/workflows/build-basic/finalize.md",
            "assets/workflows/build-basic-review/{target}.md",
            "assets/workflows/do-work/implement.md",
            "assets/workflows/do-work-item/implement-item.md",
        ):
            text = (root / relative_path).read_text(encoding="utf-8")
            for fragment in (
                "Use mapping objects for front matter",
                "`workflow: build-basic`",
                "workflow: {id: <workflow-root-id>",
                "Trace front matter must use the validator shape exactly",
                "`trace.upstream[]` entries must include `path` and `hash`",
                "do not use\n  `id`/`title`/`type` entries as the upstream shape",
                "scheme-qualified",
                "Markdown coverage table with the same status",
                "The validator only recognizes",
                "| ID | Status |",
                "Coverage statuses are not artifact statuses",
                "do not use `approved` in `trace.coverage[].status`",
            ):
                with self.subTest(asset=relative_path, fragment=fragment):
                    self.assertIn(fragment, text)

        for relative_path in (
            "assets/workflows/build-basic/finalize.md",
            "assets/workflows/build-basic-review/{target}.md",
        ):
            text = (root / relative_path).read_text(encoding="utf-8")
            self.assertIn(
                "Do not create any additional Markdown table with both an `ID` column and a",
                text,
            )

        for relative_path in (
            "assets/workflows/do-work/implement.md",
            "assets/workflows/do-work-item/implement-item.md",
            "assets/workflows/implement/summarize.md",
        ):
            text = (root / relative_path).read_text(encoding="utf-8")
            for fragment in (
                "validator only recognizes a table",
                "an `ID` column",
                "a `Status` column",
                "| ID | Status |",
            ):
                with self.subTest(asset=relative_path, fragment=fragment):
                    self.assertIn(fragment, text)

        for relative_path in (
            "assets/workflows/do-work/implement.md",
            "assets/workflows/do-work-item/implement-item.md",
            "assets/workflows/implementation-base/implement.md",
            "assets/workflows/implementation-item-base/implement-item.md",
            "assets/workflows/implement/summarize.md",
            "assets/workflows/build-base/summarize-implementation.md",
        ):
            text = (root / relative_path).read_text(encoding="utf-8")
            for fragment in (
                "read the launcher rig root from the workflow root bead's `gc.work_dir`",
                MANUAL_BUILD_ARTIFACT_CHECK_COMMAND,
                "fix every reported validation error before setting `gc.outcome=pass`",
            ):
                with self.subTest(asset=relative_path, fragment=fragment):
                    self.assertIn(fragment, text)

    SUMMARY_PATH_FRAGMENTS = (
        "outside the repo's tracked tree",
        "`{{artifact_root}}/task-<source-anchor-id>-summary.md`",
        "Resolve a relative artifact root against the launcher rig root in `gc.work_dir`",
        "Only if the artifact root is blank or an unfilled placeholder, use `$WORKTREE/.gc-artifacts/<source-anchor-id>/summary.md`; it must never be committed",
        "Record the absolute path on the workflow root bead as `gc.implementation.summary_path`",
        "Never write it at the worktree root",
        "never `git add -A` or `git add .`",
        "`git diff --cached --name-only` lists no summary, report, or other workflow artifact",
    )

    def test_per_item_implementation_summary_is_bead_scoped_and_never_committed(
        self,
    ) -> None:
        """gc-9949w: the per-item implement prompts said to write the summary
        "inside `$WORKTREE`" without naming a path, so workers wrote
        `<bead>-implementation-summary.md` at the worktree root (which
        `git add -A` committed: 14 sidecars on ascent main) or overwrote one
        shared `.gc-artifacts/implementation-summary.md` (gcas-j201cw). The
        summary now goes to the bead-scoped `{{artifact_root}}` path that
        close-source-anchor already targets, and commits carry only the change.
        Checked in all four prompts (gc-6svtga's scope) and in what the three
        build-* formulas actually render through their drains.
        """
        root = pathlib.Path(__file__).resolve().parents[1]
        for relative_path in (
            "assets/workflows/do-work/implement.md",
            "assets/workflows/do-work-item/implement-item.md",
            "assets/workflows/implementation-base/implement.md",
            "assets/workflows/implementation-item-base/implement-item.md",
        ):
            # Prose wraps at each file's own width; match on whitespace-normalized text.
            normalized = " ".join((root / relative_path).read_text(encoding="utf-8").split())
            for fragment in self.SUMMARY_PATH_FRAGMENTS:
                with self.subTest(asset=relative_path, fragment=fragment):
                    self.assertIn(fragment, normalized)
            with self.subTest(asset=relative_path, fragment="no unnamed $WORKTREE write"):
                self.assertNotIn("Write this artifact inside `$WORKTREE`", normalized)
        for formula in self.R13_BUILD_FORMULAS:
            for step, drained_step in (("implement", "implement"), ("implement-same-session", "implement-item")):
                normalized = " ".join(drained_step_text(root, formula, step, drained_step).split())
                for fragment in self.SUMMARY_PATH_FRAGMENTS:
                    with self.subTest(formula=formula, step=step, fragment=fragment):
                        self.assertIn(fragment, normalized)
        # The implement step and close-source-anchor must name one destination,
        # and close-source-anchor must keep an already-durable path as-is.
        close = " ".join(
            (root / "assets/workflows/do-work/close-source-anchor.md").read_text(encoding="utf-8").split()
        )
        self.assertIn("`{{artifact_root}}/task-<source-anchor-id>-summary.md`", close)
        self.assertIn("If the current evidence path is already outside any worktree", close)

    def test_build_basic_review_context_is_worktree_anchored(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        workflow_dir = root / "assets" / "workflows" / "build-basic-review"
        setup = (workflow_dir / "{target}.setup-build-basic-review.md").read_text(
            encoding="utf-8"
        )
        acceptance = (workflow_dir / "{target}.acceptance-review.md").read_text(
            encoding="utf-8"
        )
        test_evidence = (workflow_dir / "{target}.test-evidence-review.md").read_text(
            encoding="utf-8"
        )
        simplicity = (workflow_dir / "{target}.simplicity-review.md").read_text(
            encoding="utf-8"
        )
        synthesize = (workflow_dir / "{target}.synthesize-review.md").read_text(
            encoding="utf-8"
        )
        apply = (workflow_dir / "{target}.apply-review-findings.md").read_text(
            encoding="utf-8"
        )

        for fragment in (
            "gc.build.code_review_context_path",
            "Implementation Worktrees",
            "metadata.work_dir",
            "Do not write\nliteral command substitutions",
            r"rg -n '\$\((cat|date)'",
        ):
            with self.subTest(asset="setup", fragment=fragment):
                self.assertIn(fragment, setup)

        for asset_name, text in (
            ("acceptance", acceptance),
            ("test-evidence", test_evidence),
            ("simplicity", simplicity),
            ("synthesize", synthesize),
            ("apply", apply),
        ):
            with self.subTest(asset=asset_name, fragment="context path"):
                self.assertIn("gc.build.code_review_context_path", text)
            with self.subTest(asset=asset_name, fragment="worktree section"):
                self.assertIn("Implementation Worktrees", text)
            with self.subTest(asset=asset_name, fragment="launcher root"):
                self.assertIn(
                    "`gc.work_dir` is the launcher rig root, not the implementation worktree",
                    text,
                )

        for asset_name, text in (
            ("acceptance", acceptance),
            ("test-evidence", test_evidence),
            ("simplicity", simplicity),
            ("apply", apply),
        ):
            with self.subTest(asset=asset_name, fragment="cd worktree"):
                self.assertIn('cd "$WORKTREE"', text)
            with self.subTest(asset=asset_name, fragment="pwd verification"):
                self.assertIn("pwd -P", text)

        self.assertIn("do not patch the launcher root", apply)
        self.assertIn("source anchor and implementation\nworktree", synthesize)
    def test_build_artifact_prompts_use_set_metadata_for_paths(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        path_contracts = {
            "assets/workflows/build-base/requirements.md": ["gc.build.requirements_path"],
            "assets/workflows/build-base/plan.md": ["gc.build.plan_path"],
            "assets/workflows/build-base/decompose.md": ["gc.build.decomposition_path"],
            "assets/workflows/build-base/summarize-implementation.md": ["gc.build.implementation_summary_path"],
            "assets/workflows/build-base/review.md": ["gc.build.review_report_path"],
            "assets/workflows/build-base/finalize.md": ["gc.build.final_report_path"],
            "assets/workflows/build-basic/requirements.md": ["gc.build.requirements_path"],
            "assets/workflows/build-basic/plan.md": ["gc.build.plan_path"],
            "assets/workflows/build-basic/decompose.md": ["gc.build.decomposition_path"],
            "assets/workflows/build-basic/review.md": ["gc.build.review_report_path"],
            "assets/workflows/build-basic/finalize.md": [
                "gc.build.implementation_summary_path",
                "gc.build.final_report_path",
                "gc.build.factory_run_path",
            ],
            "assets/workflows/build-basic-review/{target}.md": ["gc.build.review_report_path"],
        }

        for relative_path, keys in path_contracts.items():
            text = (root / relative_path).read_text(encoding="utf-8")
            with self.subTest(asset=relative_path, fragment="metadata warning"):
                self.assertIn("Do not use `gc bd update --metadata 'key=value'`", text)
            for fragment in (
                'gc bd update "<claimed-step-id>" --set-metadata "gc.outcome=pass"',
                'gc bd close "<claimed-step-id>" --reason "<concise reason>"',
                "Do not pass\n`--metadata` or `--set-metadata` to `gc bd close`",
            ):
                with self.subTest(asset=relative_path, fragment=fragment):
                    self.assertIn(fragment, text)
            positive_guidance = "\n".join(
                line for line in text.splitlines() if "Do not use" not in line
            )
            self.assertIsNone(
                re.search(r"gc bd update[^`\n]*--metadata ['\"]?[A-Za-z0-9_.-]+=", positive_guidance),
                relative_path,
            )
            for key in keys:
                with self.subTest(asset=relative_path, key=key):
                    self.assertIn("--set-metadata", text)
                    self.assertIn(f"{key}=<", text)

    def test_third_party_build_packs_extend_base_and_vendor_sources(self) -> None:
        gascity_root = pathlib.Path(__file__).resolve().parents[1]
        packs_root = gascity_root.parent
        for pack_name, expected in THIRD_PARTY_BUILD_PACKS.items():
            with self.subTest(pack=pack_name):
                pack_root = packs_root / pack_name
                formula_name = expected["formula"]
                data = load_formula(pack_root, formula_name)
                resolved = resolve_formula_from_dirs(
                    [gascity_root / "formulas", pack_root / "formulas"],
                    formula_name,
                )

                self.assertEqual(data["extends"], ["build-base"])
                self.assertEqual(data["formula"], formula_name)
                self.assertEqual(data["catalog"]["name"], formula_name)
                self.assertEqual(data["vars"]["implementation_target"]["default"], expected["implementation_target"])
                for var_name, default in methodology_selector_defaults(expected).items():
                    with self.subTest(pack=pack_name, var=var_name):
                        self.assertEqual(resolved["vars"][var_name]["default"], default)
                expected_steps = BUILD_BASE_STEPS + expected.get("extra_steps", [])
                self.assertEqual([step["id"] for step in resolved["steps"]], expected_steps)
                self.assertNotIn("compound", [step["id"] for step in resolved["steps"]])
                step_by_id = {step["id"]: step for step in data["steps"]}
                if "implementation-readiness" in expected.get("extra_steps", []):
                    self.assertEqual(step_by_id["implementation-readiness"]["needs"], ["decompose"])
                    self.assertEqual(
                        step_by_id["implementation-readiness"]["metadata"]["gc.run_target"],
                        "bmad.readiness-reviewer",
                    )
                    self.assertEqual(step_by_id["implement"]["needs"], ["implementation-readiness"])
                    self.assertEqual(
                        step_by_id["implement-same-session"]["needs"],
                        ["implementation-readiness"],
                    )
                self.assertEqual(step_by_id["implement"]["metadata"]["gc.run_target"], "{{implementation_target}}")
                self.assertNotIn("expand", step_by_id["implement"])
                self.assertEqual(step_by_id["implement"]["condition"], "{{drain_policy}} == separate")
                self.assertEqual(step_by_id["implement"]["drain"]["context"], "separate")
                self.assertEqual(step_by_id["implement"]["drain"]["formula"], expected["implementation_formula"])
                self.assertEqual(step_by_id["implement"]["drain"]["member_access"], "exclusive")
                self.assertEqual(
                    step_by_id["implement-same-session"]["metadata"]["gc.run_target"],
                    "{{implementation_target}}",
                )
                self.assertEqual(
                    step_by_id["implement-same-session"]["condition"],
                    "{{drain_policy}} == same-session",
                )
                self.assertEqual(step_by_id["implement-same-session"]["drain"]["context"], "shared")
                self.assertEqual(
                    step_by_id["implement-same-session"]["drain"]["formula"],
                    expected["implementation_item_formula"],
                )
                self.assertEqual(
                    step_by_id["implement-same-session"]["drain"]["member_access"],
                    "exclusive",
                )
                self.assertEqual(
                    step_by_id["implement-same-session"]["drain"]["on_item_failure"],
                    "skip_remaining",
                )
                self.assertTrue(step_by_id["implement-same-session"]["drain"]["item"]["single_lane"])
                review_step = step_by_id["review"]
                self.assertEqual(review_step["needs"], ["summarize-implementation"])
                self.assertEqual(review_step["expand"], expected["review_expansion"])
                expected_review_expand_vars = {
                    "implementation_target": "{{implementation_target}}",
                    "review_mode": "{{review_mode}}",
                    "artifact_path_keys": "gc.build.review_report_path",
                }
                expected_review_expand_vars.update(expected.get("review_expand_vars", {}))
                self.assertEqual(
                    review_step["expand_vars"],
                    expected_review_expand_vars,
                )

                pack_data = tomllib.loads((pack_root / "pack.toml").read_text(encoding="utf-8"))
                self.assertEqual(pack_data["pack"]["name"], pack_name)
                base_import = pack_data["imports"][expected["base_import_binding"]]
                self.assertEqual(base_import["source"], expected["base_import_source"])

                vendor_root = pack_root / "vendor" / expected["vendor"]
                self.assertTrue((vendor_root / "LICENSE").is_file())
                upstream = tomllib.loads((vendor_root / "upstream.toml").read_text(encoding="utf-8"))["upstream"]
                self.assertEqual(upstream["source"], expected["upstream"])
                self.assertEqual(upstream["commit"], expected["commit"])
                self.assertEqual(upstream["license"], "MIT")

                formula_text = effective_formula_text_from_dirs(
                    [gascity_root / "formulas", pack_root / "formulas"],
                    formula_name,
                )
                for step_id, skill_name in expected["skills"].items():
                    self.assertTrue((vendor_root / "skills" / skill_name / "SKILL.md").is_file())
                    self.assertTrue((pack_root / "skills" / skill_name / "SKILL.md").is_file())
                    self.assertIn(f"assets/workflows/{formula_name}/{step_id}.md", formula_text)

                for persona_asset in expected.get("persona_assets", set()):
                    self.assertTrue((vendor_root / "agents" / persona_asset).is_file())

                for prompt_asset in expected.get("prompt_assets", set()):
                    self.assertTrue((vendor_root / prompt_asset).is_file())

                decompose_text = effective_formula_text_from_dirs(
                    [gascity_root / "formulas", pack_root / "formulas"],
                    formula_name,
                )
                if pack_name == "bmad":
                    decompose_text = (pack_root / "assets/workflows/bmad-build/decompose.md").read_text(
                        encoding="utf-8",
                    )
                for fragment in (
                    "gc.input_convoy_id",
                    "implementation convoy",
                    "workflow root bead",
                    "before closing",
                ):
                    with self.subTest(pack=pack_name, step="decompose", fragment=fragment):
                        self.assertIn(fragment, decompose_text)

    def test_third_party_build_steps_expand_native_delegation_to_gascity_formulas(self) -> None:
        gascity_root = pathlib.Path(__file__).resolve().parents[1]
        packs_root = gascity_root.parent
        for pack_name, expected in THIRD_PARTY_BUILD_PACKS.items():
            pack_root = packs_root / pack_name
            build = load_formula(pack_root, expected["formula"])
            step_by_id = {step["id"]: step for step in build["steps"]}

            for step_id, expansion_name in expected["expansions"].items():
                with self.subTest(pack=pack_name, step=step_id, expansion=expansion_name):
                    self.assertEqual(step_by_id[step_id]["expand"], expansion_name)
                    expansion = load_formula(pack_root, expansion_name)
                    self.assertEqual(expansion["formula"], expansion_name)
                    self.assertEqual(expansion["type"], "expansion")
                    self.assertEqual(expansion["contract"], "graph.v2")

                    nodes = formula_nodes(expansion)
                    self.assertGreaterEqual(len(nodes), 4)
                    text = effective_formula_text(pack_root, expansion_name)
                    self.assertIn("Gas City", text)
                    self.assertIn("Do not invoke provider-native subagents", text)
                    self.assertNotIn("Task tool (general-purpose):", text)
                    self.assertNotIn("Dispatch implementer subagent", text)

                    for node in nodes:
                        target = node.get("metadata", {}).get("gc.run_target", "")
                        if target:
                            assert_pack_or_role_route_target(
                                self,
                                target,
                                expansion.get("vars", {}),
                                pack_root,
                                pack_name,
                            )
                        description_file = node.get("description_file")
                        self.assertIsNotNone(description_file)
                        self.assertTrue((pack_root / "formulas" / description_file).resolve().is_file())

            item_formula = load_formula(pack_root, expected["implementation_formula"])
            with self.subTest(pack=pack_name, item_formula=expected["implementation_formula"]):
                self.assertEqual(item_formula["formula"], expected["implementation_formula"])
                self.assertEqual(item_formula["contract"], "graph.v2")
                self.assertEqual(item_formula["extends"], ["do-work"])
                self.assertNotEqual(item_formula.get("type"), "expansion")
                self.assertTrue(item_formula["target_required"])

                resolved_item = resolve_formula_from_dirs(
                    [gascity_root / "formulas", pack_root / "formulas"],
                    expected["implementation_formula"],
                )
                if pack_name == "superpowers":
                    resolved_steps = {step["id"]: step for step in resolved_item["steps"]}
                    self.assertEqual(
                        set(resolved_steps),
                        {
                            "prepare-worktree",
                            "implement",
                            "write-failing-test",
                            "verify-test-fails",
                            "implement-change",
                            "verify-test-passes",
                            "task-review",
                            "record-item-result",
                            "close-source-anchor",
                        },
                    )
                    self.assertEqual(
                        {step_id: step.get("needs", []) for step_id, step in resolved_steps.items()},
                        {
                            "prepare-worktree": [],
                            "implement": ["prepare-worktree"],
                            "write-failing-test": ["implement"],
                            "verify-test-fails": ["write-failing-test"],
                            "implement-change": ["verify-test-fails"],
                            "verify-test-passes": ["implement-change"],
                            "task-review": ["verify-test-passes"],
                            "record-item-result": ["task-review"],
                            "close-source-anchor": ["record-item-result"],
                        },
                    )
                else:
                    self.assertEqual(
                        [step["id"] for step in resolved_item["steps"]],
                        ["prepare-worktree", "implement", "close-source-anchor"],
                    )
                self.assertTrue(any(step["id"] == "implement" for step in item_formula["steps"]))
                text = effective_formula_text_from_dirs(
                    [gascity_root / "formulas", pack_root / "formulas"],
                    expected["implementation_formula"],
                )
                self.assertIn("Gas City", text)
                self.assertIn("Do not invoke provider-native subagents", text)
                self.assertNotIn("Task tool (general-purpose):", text)
                self.assertNotIn("Dispatch implementer subagent", text)

                for node in formula_nodes(resolved_item):
                    target = node.get("metadata", {}).get("gc.run_target", "")
                    if target:
                        assert_pack_or_role_route_target(
                            self,
                            target,
                            resolved_item.get("vars", {}),
                            pack_root,
                            pack_name,
                        )
                    description_file = node.get("description_file")
                    self.assertIsNotNone(description_file)
                    self.assertTrue(
                        any(
                            (formula_dir / description_file).resolve().is_file()
                            for formula_dir in (gascity_root / "formulas", pack_root / "formulas")
                        )
                    )

            shared_item_formula = load_formula(pack_root, expected["implementation_item_formula"])
            with self.subTest(pack=pack_name, item_formula=expected["implementation_item_formula"]):
                self.assertEqual(shared_item_formula["formula"], expected["implementation_item_formula"])
                self.assertEqual(shared_item_formula["contract"], "graph.v2")
                self.assertEqual(shared_item_formula["extends"], ["do-work-item"])
                self.assertNotEqual(shared_item_formula.get("type"), "expansion")
                self.assertTrue(shared_item_formula["target_required"])
                self.assertTrue(shared_item_formula["internal"])
                self.assertTrue(shared_item_formula["single_lane"])

                resolved_shared = resolve_formula_from_dirs(
                    [gascity_root / "formulas", pack_root / "formulas"],
                    expected["implementation_item_formula"],
                )
                if pack_name == "superpowers":
                    resolved_steps = {step["id"]: step for step in resolved_shared["steps"]}
                    self.assertEqual(
                        set(resolved_steps),
                        {
                            "implement-item",
                            "write-failing-test",
                            "verify-test-fails",
                            "implement-change",
                            "verify-test-passes",
                            "task-review",
                            "record-item-result",
                            "close-source-anchor",
                        },
                    )
                    self.assertEqual(
                        {step_id: step.get("needs", []) for step_id, step in resolved_steps.items()},
                        {
                            "implement-item": [],
                            "write-failing-test": ["implement-item"],
                            "verify-test-fails": ["write-failing-test"],
                            "implement-change": ["verify-test-fails"],
                            "verify-test-passes": ["implement-change"],
                            "task-review": ["verify-test-passes"],
                            "record-item-result": ["task-review"],
                            "close-source-anchor": ["record-item-result"],
                        },
                    )
                else:
                    self.assertEqual([step["id"] for step in resolved_shared["steps"]], ["implement-item"])
                self.assertTrue(any(step["id"] == "implement-item" for step in shared_item_formula["steps"]))
                text = effective_formula_text_from_dirs(
                    [gascity_root / "formulas", pack_root / "formulas"],
                    expected["implementation_item_formula"],
                )
                self.assertIn("Gas City", text)
                self.assertIn("Do not invoke provider-native subagents", text)
                self.assertNotIn("Task tool (general-purpose):", text)
                self.assertNotIn("Dispatch implementer subagent", text)

                for node in formula_nodes(resolved_shared):
                    target = node.get("metadata", {}).get("gc.run_target", "")
                    if target:
                        assert_pack_or_role_route_target(
                            self,
                            target,
                            resolved_shared.get("vars", {}),
                            pack_root,
                            pack_name,
                        )
                    description_file = node.get("description_file")
                    self.assertIsNotNone(description_file)
                    self.assertTrue(
                        any(
                            (formula_dir / description_file).resolve().is_file()
                            for formula_dir in (gascity_root / "formulas", pack_root / "formulas")
                        )
                    )

            review_expansion = load_formula(pack_root, expected["review_expansion"])
            with self.subTest(pack=pack_name, expansion=expected["review_expansion"], route="review-fix"):
                self.assertEqual(
                    review_expansion["vars"]["implementation_target"]["default"],
                    expected["implementation_target"],
                )
                self.assertNotIn("drain_policy", review_expansion["vars"])
                review_fix_targets = [
                    node.get("metadata", {}).get("gc.run_target")
                    for node in formula_nodes(review_expansion)
                    if node.get("metadata", {}).get("gc.continuation_group", "").endswith("fixes")
                ]
                self.assertIn("{implementation_target}", review_fix_targets)
                gap_targets = [
                    node.get("metadata", {}).get("gc.run_target")
                    for node in formula_nodes(review_expansion)
                    if node["id"].endswith(".gap-analysis-review")
                ]
                self.assertEqual(gap_targets, [expected["gap_analysis_target"]])

    def test_third_party_methodology_contract_wrappers_are_adapter_selectable(self) -> None:
        gascity_root = pathlib.Path(__file__).resolve().parents[1]
        packs_root = gascity_root.parent
        for pack_name, expected in THIRD_PARTY_BUILD_PACKS.items():
            pack_root = packs_root / pack_name
            formula_dirs = [gascity_root / "formulas", pack_root / "formulas"]

            planning = load_formula(pack_root, expected["planning_formula"])
            with self.subTest(pack=pack_name, formula=expected["planning_formula"]):
                self.assertEqual(planning["extends"], ["planning-base"])
                self.assertFalse(planning["target_required"])
                self.assertTrue(planning["internal"])
                self.assertNotIn("catalog", planning)
                resolved = resolve_formula_from_dirs(formula_dirs, expected["planning_formula"])
                self.assertEqual(
                    [step["id"] for step in resolved["steps"]],
                    METHODOLOGY_STAGE_CONTRACTS["planning-base"]["steps"],
                )

            decomposition = load_formula(pack_root, expected["decomposition_formula"])
            with self.subTest(pack=pack_name, formula=expected["decomposition_formula"]):
                self.assertEqual(decomposition["extends"], ["decomposition-base"])
                self.assertFalse(decomposition["target_required"])
                self.assertTrue(decomposition["internal"])
                resolved = resolve_formula_from_dirs(formula_dirs, expected["decomposition_formula"])
                self.assertIn("decompose", [step["id"] for step in resolved["steps"]])
                if pack_name == "bmad":
                    self.assertIn("implementation-readiness", [step["id"] for step in resolved["steps"]])

            implementation = load_formula(pack_root, expected["implementation_entry_formula"])
            with self.subTest(pack=pack_name, formula=expected["implementation_entry_formula"]):
                self.assertEqual(implementation["extends"], ["implement"])
                self.assertTrue(implementation["target_required"])
                self.assertTrue(implementation["internal"])
                self.assertEqual(
                    implementation["vars"]["implementation_target"]["default"],
                    expected["implementation_target"],
                )
                steps = {step["id"]: step for step in implementation["steps"]}
                self.assertEqual(steps["drain-separate"]["drain"]["formula"], expected["implementation_formula"])
                self.assertEqual(
                    steps["drain-same-session"]["drain"]["formula"],
                    expected["implementation_item_formula"],
                )

            review = load_formula(pack_root, expected["code_review_entry_formula"])
            with self.subTest(pack=pack_name, formula=expected["code_review_entry_formula"]):
                self.assertEqual(review["extends"], ["code-review-base"])
                self.assertFalse(review["target_required"])
                self.assertTrue(review["internal"])
                self.assertEqual(review["mode"], "report")
                self.assertEqual(
                    review["vars"]["implementation_target"]["default"],
                    expected["implementation_target"],
                )
                write_report = next(step for step in review["steps"] if step["id"] == "write-report")
                self.assertEqual(write_report["expand"], expected["review_expansion"])
                expected_review_expand_vars = {
                    "implementation_target": "{{implementation_target}}",
                    "review_mode": "{{review_mode}}",
                }
                expected_review_expand_vars.update(
                    expected.get(
                        "code_review_entry_expand_vars",
                        expected.get("review_expand_vars", {}),
                    )
                )
                self.assertEqual(
                    write_report["expand_vars"],
                    expected_review_expand_vars,
                )
                text = effective_formula_text_from_dirs(formula_dirs, expected["code_review_entry_formula"])
                for fragment in ("{{subject_path}}", "{{report_path}}", "{{context_path}}"):
                    self.assertIn(fragment, text)

            fix_loop = load_formula(pack_root, expected["review_fix_formula"])
            with self.subTest(pack=pack_name, formula=expected["review_fix_formula"]):
                self.assertEqual(fix_loop["extends"], ["fix-loop-base"])
                self.assertFalse(fix_loop["target_required"])
                self.assertTrue(fix_loop["internal"])
                self.assertEqual(
                    fix_loop["vars"]["implementation_formula"]["default"],
                    expected["implementation_entry_formula"],
                )
                self.assertEqual(
                    fix_loop["vars"]["code_review_formula"]["default"],
                    expected["code_review_entry_formula"],
                )
                self.assertEqual(
                    fix_loop["vars"]["implementation_target"]["default"],
                    expected["implementation_target"],
                )

    def test_gstack_build_pack_models_garrytan_sprint_with_gascity_fanouts(self) -> None:
        gascity_root = pathlib.Path(__file__).resolve().parents[1]
        packs_root = gascity_root.parent
        pack_root = packs_root / "gstack"
        formula_dirs = [gascity_root / "formulas", pack_root / "formulas"]

        build = load_formula(pack_root, "gstack-build")
        resolved = resolve_formula_from_dirs(formula_dirs, "gstack-build")
        step_by_id = {step["id"]: step for step in build["steps"]}

        self.assertEqual(build["extends"], ["build-base"])
        self.assertEqual([step["id"] for step in resolved["steps"]], BUILD_BASE_STEPS + ["qa", "release-readiness"])
        self.assertEqual(build["vars"]["interaction_mode"]["default"], "interactive")
        self.assertEqual(build["vars"]["review_mode"]["default"], "interactive")
        self.assertEqual(step_by_id["requirements"]["metadata"]["gc.run_target"], "gstack.office-hours")
        self.assertEqual(step_by_id["plan-review"]["expand"], "gstack-plan-review")
        self.assertEqual(step_by_id["qa"]["expand"], "gstack-qa-review")
        self.assertEqual(step_by_id["release-readiness"]["expand"], "gstack-release-readiness")
        self.assertEqual(step_by_id["finalize"]["needs"], ["release-readiness"])

        plan_review = load_formula(pack_root, "gstack-plan-review")
        plan_loop = {
            template["id"]: template
            for template in plan_review["template"]
        }["{target}.gstack-plan-review-loop"]
        self.assertEqual(
            [child["id"] for child in plan_loop["children"]],
            [
                "{target}.founder-scope-review",
                "{target}.design-plan-review",
                "{target}.engineering-plan-review",
                "{target}.devex-plan-review",
                "{target}.synthesize-plan-review",
                "{target}.apply-plan-review-findings",
            ],
        )
        for target in (
            "gstack.founder-reviewer",
            "gstack.design-reviewer",
            "gstack.eng-reviewer",
            "gstack.devex-reviewer",
        ):
            with self.subTest(expansion="plan-review", target=target):
                self.assertIn(
                    target,
                    [child["metadata"]["gc.run_target"] for child in plan_loop["children"] if "gc.run_target" in child["metadata"]],
                )
        self.assertEqual(
            plan_loop["children"][-1]["metadata"]["gc.continuation_group"],
            "gstack-plan-review-fixes",
        )

        code_review = load_formula(pack_root, "gstack-code-review")
        code_loop = {
            template["id"]: template
            for template in code_review["template"]
        }["{target}.gstack-code-review-loop"]
        self.assertEqual(
            [child["id"] for child in code_loop["children"]],
            [
                "{target}.staff-code-review",
                "{target}.qa-evidence-review",
                "{target}.security-review",
                "{target}.gap-analysis-review",
                "{target}.synthesize-code-review",
                "{target}.apply-review-findings",
            ],
        )
        for target in (
            "gstack.staff-reviewer",
            "gstack.qa-lead",
            "gstack.security-officer",
        ):
            with self.subTest(expansion="code-review", target=target):
                self.assertIn(
                    target,
                    [child["metadata"]["gc.run_target"] for child in code_loop["children"] if "gc.run_target" in child["metadata"]],
                )

        qa = load_formula(pack_root, "gstack-qa-review")
        qa_loop = {
            template["id"]: template
            for template in qa["template"]
        }["{target}.gstack-qa-loop"]
        self.assertEqual(
            [child["id"] for child in qa_loop["children"]],
            [
                "{target}.browser-qa",
                "{target}.regression-test-review",
                "{target}.qa-fix-findings",
                "{target}.synthesize-qa",
            ],
        )
        self.assertEqual(
            qa_loop["children"][2]["metadata"]["gc.continuation_group"],
            "gstack-qa-fixes",
        )

        release = load_formula(pack_root, "gstack-release-readiness")
        release_loop = {
            template["id"]: template
            for template in release["template"]
        }["{target}.gstack-release-readiness-loop"]
        self.assertEqual(
            [child["id"] for child in release_loop["children"]],
            [
                "{target}.document-release",
                "{target}.ship-readiness",
                "{target}.deployment-readiness",
                "{target}.synthesize-release-readiness",
            ],
        )

        asset_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((pack_root / "assets" / "workflows").glob("**/*.md"))
        )
        for fragment in (
            "garrytan/gstack",
            "Think -> Plan -> Build -> Review -> Test -> Ship -> Reflect",
            "office-hours",
            "plan-ceo-review",
            "plan-eng-review",
            "plan-design-review",
            "plan-devex-review",
            "review",
            "qa",
            "cso",
            "ship",
            "land-and-deploy",
            "document-release",
            "interaction_mode",
            "review_mode",
            "Do not invoke provider-native subagents",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, asset_text)

        readme = (pack_root / "README.md").read_text(encoding="utf-8")
        for fragment in (
            "garrytan/gstack",
            "`gstack-build`",
            "Think -> Plan -> Build -> Review -> Test -> Ship -> Reflect",
            "Gas City fanouts",
            "`interaction_mode`",
            "`review_mode`",
        ):
            with self.subTest(readme=fragment):
                self.assertIn(fragment, readme)

    def test_github_adapter_methodology_selector_matrix_covers_all_toolkits(self) -> None:
        gascity_root = pathlib.Path(__file__).resolve().parents[1]
        packs_root = gascity_root.parent
        scenarios = {
            "gascity": {
                "formula_dirs": [gascity_root / "formulas"],
                "selectors": METHODOLOGY_FORMULA_VARS,
                "implementation_target": "gc.implementation-worker",
                "separate_item_formula": "do-work",
                "pack_root": gascity_root,
            },
        }
        for pack_name, expected in THIRD_PARTY_BUILD_PACKS.items():
            pack_root = packs_root / pack_name
            scenarios[pack_name] = {
                "formula_dirs": [gascity_root / "formulas", pack_root / "formulas"],
                "selectors": methodology_selector_defaults(expected),
                "implementation_target": expected["implementation_target"],
                "separate_item_formula": expected["implementation_formula"],
                "pack_root": pack_root,
            }

        for toolkit, scenario in scenarios.items():
            with self.subTest(toolkit=toolkit):
                formula_dirs = scenario["formula_dirs"]
                selectors = scenario["selectors"]
                implementation_target = scenario["implementation_target"]
                separate_item_formula = scenario["separate_item_formula"]

                pr_adapter = resolve_formula_from_dirs(formula_dirs, "github-pr-review")
                issue_adapter = resolve_formula_from_dirs(formula_dirs, "github-issue-fix")
                self.assertFalse(pr_adapter["target_required"])
                self.assertFalse(issue_adapter["target_required"])
                pr_routes = {step["id"]: step["metadata"]["gc.run_target"] for step in pr_adapter["steps"]}
                issue_routes = {step["id"]: step["metadata"]["gc.run_target"] for step in issue_adapter["steps"]}
                self.assertEqual(pr_routes["run-review"], "gc.run-operator")
                self.assertEqual(issue_routes["build"], "gc.run-operator")

                pr_launch = {
                    "github_pr_url": "https://github.com/example/project/pull/123",
                    "code_review_formula": selectors["code_review_formula"],
                    "interaction_mode": "autonomous",
                    "review_mode": "report",
                }
                issue_launch = {
                    "github_issue_url": "https://github.com/example/project/issues/456",
                    "planning_formula": selectors["planning_formula"],
                    "decomposition_formula": selectors["decomposition_formula"],
                    "implementation_formula": selectors["implementation_formula"],
                    "implementation_item_formula": selectors["implementation_item_formula"],
                    "code_review_formula": selectors["code_review_formula"],
                    "review_fix_formula": selectors["review_fix_formula"],
                    "implementation_target": implementation_target,
                    "interaction_mode": "autonomous",
                    "review_mode": "agent",
                    "drain_policy": "separate",
                }
                for var_name in pr_launch:
                    self.assertIn(var_name, pr_adapter["vars"])
                for var_name in issue_launch:
                    self.assertIn(var_name, issue_adapter["vars"])

                planning = resolve_formula_from_dirs(formula_dirs, selectors["planning_formula"])
                self.assertFalse(planning["target_required"])
                self.assertEqual(
                    [step["id"] for step in planning["steps"]],
                    METHODOLOGY_STAGE_CONTRACTS["planning-base"]["steps"],
                )

                decomposition = resolve_formula_from_dirs(formula_dirs, selectors["decomposition_formula"])
                self.assertFalse(decomposition["target_required"])
                self.assertIn("decompose", [step["id"] for step in decomposition["steps"]])

                implementation = resolve_formula_from_dirs(formula_dirs, selectors["implementation_formula"])
                self.assertTrue(implementation["target_required"])
                implementation_steps = {step["id"]: step for step in implementation["steps"]}
                self.assertEqual(implementation_steps["drain-separate"]["drain"]["formula"], separate_item_formula)
                self.assertEqual(
                    implementation_steps["drain-same-session"]["drain"]["formula"],
                    selectors["implementation_item_formula"],
                )

                implementation_item = resolve_formula_from_dirs(
                    formula_dirs,
                    selectors["implementation_item_formula"],
                )
                self.assertTrue(implementation_item["target_required"])
                self.assertIn("implement-item", [step["id"] for step in implementation_item["steps"]])

                code_review_raw = load_formula_from_dirs(formula_dirs, selectors["code_review_formula"])
                code_review = resolve_formula_from_dirs(formula_dirs, selectors["code_review_formula"])
                self.assertFalse(code_review_raw["target_required"])
                self.assertEqual(code_review_raw["mode"], "report")
                for var_name in ("context_path", "subject_path", "report_path"):
                    self.assertIn(var_name, code_review["vars"])

                fix_loop = resolve_formula_from_dirs(formula_dirs, selectors["review_fix_formula"])
                self.assertFalse(fix_loop["target_required"])
                self.assertEqual(
                    fix_loop["vars"]["implementation_formula"]["default"],
                    selectors["implementation_formula"],
                )
                self.assertEqual(
                    fix_loop["vars"]["code_review_formula"]["default"],
                    selectors["code_review_formula"],
                )
                self.assertEqual(fix_loop["vars"]["implementation_target"]["default"], implementation_target)

                pr_text = effective_formula_text_from_dirs(formula_dirs, "github-pr-review")
                issue_text = effective_formula_text_from_dirs(formula_dirs, "github-issue-fix")
                self.assertIn("{{code_review_formula}}", pr_text)
                for var_name in selectors:
                    self.assertIn(f"{{{{{var_name}}}}}", issue_text)

                if toolkit != "gascity":
                    pack_root = scenario["pack_root"]
                    self.assertFalse((pack_root / "formulas" / "github-pr-review.formula.toml").exists())
                    self.assertFalse((pack_root / "formulas" / "github-issue-fix.formula.toml").exists())

    def test_superpowers_decomposition_keeps_procedure_in_drain_formula(self) -> None:
        packs_root = pathlib.Path(__file__).resolve().parents[2]
        pack_root = packs_root / "superpowers"
        build = load_formula(pack_root, "superpowers-build")
        step_by_id = {step["id"]: step for step in build["steps"]}

        self.assertIn("decompose", step_by_id)
        decompose_text = (
            pack_root / "assets" / "workflows" / "superpowers-build" / "decompose.md"
        ).read_text(encoding="utf-8")
        for fragment in (
            "Do not copy the plan checkbox steps into the implementation bead",
            "do not create implementation beads for Superpowers build",
            "actual source-code work from the original input task",
            "gc.input_convoy_id",
            "implementation convoy",
            "workflow root bead",
            "before closing",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, decompose_text)

        plan_text = (
            pack_root / "assets" / "workflows" / "superpowers-build" / "plan.md"
        ).read_text(encoding="utf-8")
        for fragment in (
            "Do not write `prepare`, `requirements`, `plan`",
            "Only `### Task N` sections are decomposed into implementation beads",
            "input task or convoy member",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, plan_text)

        plan_review_text = (
            pack_root
            / "assets"
            / "workflows"
            / "superpowers-plan-review"
            / "{target}.plan-document-review.md"
        ).read_text(encoding="utf-8")
        for fragment in (
            "Reject with `design_review.review_verdict=iterate`",
            "must not become implementation beads",
            "original input task or convoy member",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, plan_review_text)

        for formula_name in ("superpowers-development", "superpowers-development-item"):
            formula = load_formula(pack_root, formula_name)
            text = effective_formula_text_from_dirs(
                [packs_root / "gascity" / "formulas", pack_root / "formulas"],
                formula_name,
            )
            with self.subTest(formula=formula_name):
                self.assertIn("test-driven-development", text)
                self.assertIn("superpowers-task-{{issue}}", text)
                self.assertNotIn("superpowers-spec-fixes", text)
                self.assertNotIn("superpowers-quality-fixes", text)
                continuation_groups = [
                    node.get("metadata", {}).get("gc.continuation_group", "")
                    for node in formula_nodes(formula)
                    if node.get("metadata", {}).get("gc.continuation_group")
                ]
                self.assertGreaterEqual(len(continuation_groups), 5)
                self.assertTrue(
                    all(group == "superpowers-task-{{issue}}" for group in continuation_groups)
                )

    def test_superpowers_finalizer_materializes_canonical_build_summary(self) -> None:
        packs_root = pathlib.Path(__file__).resolve().parents[2]
        pack_root = packs_root / "superpowers"
        build = load_formula(pack_root, "superpowers-build")
        finalize_step = {step["id"]: step for step in build["steps"]}["finalize"]
        finalize = (
            pack_root / "assets" / "workflows" / "superpowers-build" / "finalize.md"
        ).read_text(encoding="utf-8")

        self.assertEqual(finalize_step["metadata"]["gc.run_target"], "superpowers.finisher")
        for fragment in (
            "materialize the canonical",
            "gc.build.implementation_summary_path",
            "implementation-summary.md",
            "{{artifact_root}}",
            "gc.build.implementation-summary.v1",
            "gc.implementation.summary_path",
            "source anchors",
            "gc bd update",
            "Do not create the final report",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, finalize)

    def test_superpowers_development_converts_subagent_reviews_to_fanout(self) -> None:
        packs_root = pathlib.Path(__file__).resolve().parents[2]
        pack_root = packs_root / "superpowers"
        formula_dirs = [packs_root / "gascity" / "formulas", pack_root / "formulas"]
        review = load_formula(pack_root, "superpowers-task-review")

        self.assertEqual(review["type"], "expansion")
        templates = {template["id"]: template for template in review["template"]}
        loop = templates["{target}.superpowers-task-review-loop"]
        self.assertEqual(
            [child["id"] for child in loop["children"]],
            [
                "{target}.spec-compliance-review",
                "{target}.apply-spec-compliance-findings",
                "{target}.code-quality-review",
                "{target}.apply-code-quality-findings",
            ],
        )
        self.assertEqual(
            loop["children"][0]["metadata"]["gc.run_target"],
            "superpowers.spec-reviewer",
        )
        self.assertEqual(
            loop["children"][2]["metadata"]["gc.run_target"],
            "superpowers.code-quality-reviewer",
        )
        self.assertEqual(
            loop["children"][3]["metadata"]["gc.continuation_group"],
            "superpowers-item-quality-fixes",
        )

        for formula_name, review_step_id in (
            ("superpowers-development", "task-review"),
            ("superpowers-development-item", "task-review"),
        ):
            with self.subTest(formula=formula_name):
                text = effective_formula_text_from_dirs(formula_dirs, formula_name)
                self.assertIn('expand = "superpowers-task-review"', text)
                self.assertIn(
                    'expand_vars = { implementation_target = "{{implementation_target}}" }',
                    text,
                )
                formula = load_formula(pack_root, formula_name)
                steps = {step["id"]: step for step in formula["steps"]}
                self.assertIn(review_step_id, steps)
                self.assertEqual(steps[review_step_id]["needs"], ["verify-test-passes"])
                self.assertEqual(steps["record-item-result"]["needs"], [review_step_id])

        asset_root = pack_root / "assets" / "workflows" / "superpowers-task-review"
        asset_text = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(asset_root.glob("*.md"))
        )
        for fragment in (
            "Gas City fanout lane",
            "Do not invoke provider-native subagents",
            "spec compliance",
            "code quality",
            "code_review.verdict=done|iterate",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, asset_text)

    def test_superpowers_brainstorming_expansion_preserves_stock_loops(self) -> None:
        packs_root = pathlib.Path(__file__).resolve().parents[2]
        pack_root = packs_root / "superpowers"
        formula = load_formula(pack_root, "superpowers-brainstorming")
        templates = {template["id"]: template for template in formula["template"]}

        self.assertEqual(
            [template["id"] for template in formula["template"]],
            [
                "{target}.setup-superpowers-brainstorming",
                "{target}.superpowers-design-approval-loop",
                "{target}.superpowers-written-spec-loop",
                "{target}",
            ],
        )

        design_loop = templates["{target}.superpowers-design-approval-loop"]
        self.assertEqual(design_loop["needs"], ["{target}.setup-superpowers-brainstorming"])
        self.assertEqual(design_loop["check"]["max_attempts"], 6)
        self.assertEqual(
            design_loop["check"]["check"],
            {
                "mode": "exec",
                "path": "../assets/scripts/checks/design-review-approved.sh",
                "timeout": "10m",
            },
        )
        self.assertEqual(
            [child["id"] for child in design_loop["children"]],
            ["{target}.brainstorm-design", "{target}.confirm-design-approval"],
        )
        self.assertEqual(
            design_loop["children"][1]["metadata"]["gc.continuation_group"],
            "superpowers-design-fixes",
        )

        spec_loop = templates["{target}.superpowers-written-spec-loop"]
        self.assertEqual(spec_loop["needs"], ["{target}.superpowers-design-approval-loop"])
        self.assertEqual(spec_loop["check"]["max_attempts"], 6)
        self.assertEqual(
            spec_loop["check"]["check"],
            {
                "mode": "exec",
                "path": "../assets/scripts/checks/design-review-approved.sh",
                "timeout": "10m",
            },
        )
        self.assertEqual(
            [child["id"] for child in spec_loop["children"]],
            [
                "{target}.write-requirements-spec",
                "{target}.review-written-spec",
                "{target}.apply-spec-feedback",
                "{target}.confirm-spec-approval",
            ],
        )
        self.assertEqual(
            spec_loop["children"][-1]["metadata"]["gc.continuation_group"],
            "superpowers-spec-fixes",
        )

        design_approval = (
            pack_root
            / "assets"
            / "workflows"
            / "superpowers-brainstorming"
            / "{target}.confirm-design-approval.md"
        ).read_text(encoding="utf-8")
        write_spec = (
            pack_root
            / "assets"
            / "workflows"
            / "superpowers-brainstorming"
            / "{target}.write-requirements-spec.md"
        ).read_text(encoding="utf-8")
        spec_approval = (
            pack_root
            / "assets"
            / "workflows"
            / "superpowers-brainstorming"
            / "{target}.confirm-spec-approval.md"
        ).read_text(encoding="utf-8")
        apply_spec_feedback = (
            pack_root
            / "assets"
            / "workflows"
            / "superpowers-brainstorming"
            / "{target}.apply-spec-feedback.md"
        ).read_text(encoding="utf-8")
        final_requirements = (
            pack_root
            / "assets"
            / "workflows"
            / "superpowers-brainstorming"
            / "{target}.md"
        ).read_text(encoding="utf-8")

        self.assertIn("design_review.verdict=done|iterate", design_approval)
        self.assertIn("stock `User approves design?` gate", design_approval)
        self.assertIn("Use `done` only after explicit approval", design_approval)
        self.assertIn("gc session wait", design_approval)
        self.assertIn("send exactly one mail", design_approval)
        self.assertIn("gc mail send human", design_approval)
        self.assertIn("gc.build.design_gate_mail_sent=true", design_approval)
        self.assertIn("waiting-human", design_approval)
        self.assertIn("silence", design_approval)
        self.assertIn("re-opens the design loop", design_approval)
        self.assertIn("revision summary", design_approval)
        self.assertIn("specific design sections", design_approval)
        self.assertIn('gc bd update "$CLAIMED_BEAD_ID"', design_approval)
        self.assertIn("Do not pass `--metadata` or `--set-metadata` to `gc bd close`", design_approval)
        self.assertIn("stock Superpowers checklist items 6-7", write_spec)
        self.assertIn("Spec self-review", write_spec)
        self.assertIn("stock design-doc state", write_spec)
        self.assertIn("docs/superpowers/specs/", write_spec)
        self.assertIn("On repeated attempts", write_spec)
        self.assertIn("without clobbering loop feedback", write_spec)
        self.assertIn('gc bd update "$CLAIMED_BEAD_ID"', write_spec)
        self.assertIn("Do not pass `--metadata` or `--set-metadata` to `gc bd close`", write_spec)
        self.assertIn("written spec", spec_approval)
        self.assertIn("stock `User reviews spec?` approval gate", spec_approval)
        self.assertIn("stock checklist item 8", spec_approval)
        self.assertIn("change request loops back through the written spec pass", spec_approval)
        self.assertIn("transition to downstream planning", spec_approval)
        self.assertIn("design_review.verdict=done|iterate", spec_approval)
        self.assertIn("Use `done` only after explicit approval", spec_approval)
        self.assertIn("gc session wait", spec_approval)
        self.assertIn("send exactly one mail", spec_approval)
        self.assertIn("gc mail send human", spec_approval)
        self.assertIn("gc.build.spec_gate_mail_sent=true", spec_approval)
        self.assertIn("waiting-human", spec_approval)
        self.assertIn("silence", spec_approval)
        self.assertIn("spec revision summary", spec_approval)
        self.assertIn("Do not run `../assets/scripts/checks/design-review-approved.sh`", spec_approval)
        self.assertIn("Do not use\n`gc bd update --metadata`", spec_approval)
        self.assertIn("--metadata-field gc.step_id=requirements.review-written-spec", spec_approval)
        self.assertIn("--metadata-field gc.step_id=requirements.apply-spec-feedback", spec_approval)
        self.assertIn("--metadata-field gc.scope_role=member", spec_approval)
        self.assertIn("Do not use `gc bd list --root`", spec_approval)
        self.assertIn('gc bd update "$CLAIMED_BEAD_ID"', spec_approval)
        self.assertIn('gc bd show "$CLAIMED_BEAD_ID" --json', spec_approval)
        self.assertIn("design_review.approval_mode=autonomous", spec_approval)
        self.assertIn("design_review.output_path=<approval-summary path>", spec_approval)
        self.assertIn('if type == "array" then .[0] else . end', spec_approval)
        self.assertIn('design_review.verdict == "done"', spec_approval)
        self.assertIn("Do not pass `--metadata` or `--set-metadata` to `gc bd close`", spec_approval)
        self.assertIn('gc bd update "$CLAIMED_BEAD_ID"', apply_spec_feedback)
        self.assertIn("Do not pass `--metadata` or `--set-metadata` to `gc bd close`", apply_spec_feedback)
        self.assertIn("stock brainstorming terminal state", final_requirements)
        self.assertIn("where Superpowers\nwould invoke `writing-plans`", final_requirements)
        self.assertIn("stock checklist item 9", final_requirements)
        self.assertIn("do not invoke that skill directly", final_requirements)
        self.assertIn("let the parent `superpowers-build` plan step", final_requirements)

        brainstorm_design = (
            pack_root
            / "assets"
            / "workflows"
            / "superpowers-brainstorming"
            / "{target}.brainstorm-design.md"
        ).read_text(encoding="utf-8")
        for fragment in (
            "stock Superpowers checklist items 1-5",
            "project context inspected",
            "Offer Visual Companion",
            "own message",
            "installed Visual\n  Companion guidance",
            "one clarifying question at a time",
            "two or three approaches",
            "recommended design presented in sections",
            "On repeated attempts",
            "revise that candidate in place",
            "unapproved",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, brainstorm_design)
        self.assertIn('gc bd update "$CLAIMED_BEAD_ID"', brainstorm_design)
        self.assertIn("Do not pass `--metadata` or `--set-metadata` to `gc bd close`", brainstorm_design)

        review_written_spec = (
            pack_root
            / "assets"
            / "workflows"
            / "superpowers-brainstorming"
            / "{target}.review-written-spec.md"
        ).read_text(encoding="utf-8")
        self.assertIn("stock spec reviewer subagent as a Gas City graph lane", review_written_spec)
        self.assertIn("spec-document-reviewer-prompt.md", review_written_spec)
        self.assertIn('gc bd update "$CLAIMED_BEAD_ID"', review_written_spec)
        self.assertIn("Do not pass `--metadata` or `--set-metadata` to `gc bd close`", review_written_spec)

        vendor_skill_root = pack_root / "vendor" / "superpowers" / "skills" / "brainstorming"
        installed_skill_root = pack_root / "skills" / "brainstorming"
        for relative_path in (
            "SKILL.md",
            "spec-document-reviewer-prompt.md",
            "visual-companion.md",
        ):
            with self.subTest(asset=relative_path):
                self.assertEqual(
                    (installed_skill_root / relative_path).read_text(encoding="utf-8"),
                    (vendor_skill_root / relative_path).read_text(encoding="utf-8"),
                )

        for relative_path in (
            "scripts/frame-template.html",
            "scripts/helper.js",
            "scripts/server.cjs",
            "scripts/start-server.sh",
            "scripts/stop-server.sh",
        ):
            with self.subTest(asset=relative_path):
                installed_path = installed_skill_root / relative_path
                self.assertTrue(installed_path.exists())

        for relative_path in ("scripts/start-server.sh", "scripts/stop-server.sh"):
            with self.subTest(executable=relative_path):
                self.assertTrue(os.access(installed_skill_root / relative_path, os.X_OK))

    def test_third_party_workflow_assets_guard_against_native_subagent_execution(self) -> None:
        gascity_root = pathlib.Path(__file__).resolve().parents[1]
        packs_root = gascity_root.parent
        forbidden_active_delegation = (
            "also use `{{pack_root}}/vendor/superpowers/skills/subagent-driven-development/SKILL.md`",
            "Hand `{spec_file}` to a sub-agent/task and let it implement",
            "Dispatch implementer subagent",
            "Task tool (general-purpose):",
            "{{pack_root}}/vendor",
            "{{pack_root}}/assets/scripts",
            "/SKILL.md",
            "Launch or reuse",
            "base `implement` formula",
            "read vendored files by path",
            "formula expansion is required",
            "formula already created",
        )

        for pack_name, expected in THIRD_PARTY_BUILD_PACKS.items():
            with self.subTest(pack=pack_name):
                pack_root = packs_root / pack_name
                asset_text = "\n".join(
                    path.read_text(encoding="utf-8")
                    for path in sorted((pack_root / "assets" / "workflows").glob("**/*.md"))
                )
                self.assertIn("Do not invoke provider-native subagents", asset_text)
                for phrase in forbidden_active_delegation:
                    self.assertNotIn(phrase, asset_text)

                implement_asset = (
                    pack_root / "assets" / "workflows" / expected["formula"] / "implement.md"
                ).read_text(encoding="utf-8")
                self.assertIn("{{implementation_target}}", implement_asset)
                self.assertIn("assigned", implement_asset)
                self.assertIn("implementation", implement_asset)
                self.assertIn("convoy", implement_asset)
                self.assertNotIn("expensive", implement_asset)

                review_fix_asset = (pack_root / expected["review_fix_asset"]).read_text(encoding="utf-8")
                for fragment in (
                    "{{implementation_target}}",
                    "review-fix artifact",
                    "Do not invoke provider-native subagents",
                    "graph lane is the delegation\nmechanism",
                ):
                    with self.subTest(pack=pack_name, asset=expected["review_fix_asset"], fragment=fragment):
                        self.assertIn(fragment, review_fix_asset)

                build_text = effective_formula_text_from_dirs(
                    [gascity_root / "formulas", pack_root / "formulas"],
                    expected["formula"],
                )
                for step_id, expansion_name in expected["expansions"].items():
                    with self.subTest(pack=pack_name, step=step_id):
                        self.assertIn(f'expand = "{expansion_name}"', build_text)
                        self.assertIn(f"assets/workflows/{expected['formula']}/{step_id}.md", build_text)
                self.assertIn(f'formula = "{expected["implementation_formula"]}"', build_text)
                self.assertIn(f'formula = "{expected["implementation_item_formula"]}"', build_text)

    def test_methodology_readmes_explain_modes_and_fanout_conversion(self) -> None:
        packs_root = pathlib.Path(__file__).resolve().parents[2]
        root_readme = (packs_root / "README.md").read_text(encoding="utf-8")
        gascity_readme = (packs_root / "gascity" / "README.md").read_text(encoding="utf-8")

        for fragment in (
            "Raw-framework subagents become Gas City fanouts",
            "`interaction_mode`",
            "`review_mode`",
        ):
            with self.subTest(readme="root", fragment=fragment):
                self.assertIn(fragment, root_readme)
            with self.subTest(readme="gascity", fragment=fragment):
                self.assertIn(fragment, gascity_readme)

        pack_expectations = {
            "superpowers": (
                "Superpowers task review",
                "`superpowers-task-review`",
                "spec-compliance and code-quality fanout lanes",
            ),
            "compound-engineering": (
                "Compound review fanout",
                "report-only adapter runs",
                "interactive direct builds",
            ),
            "bmad": (
                "BMAD structured steps",
                "step-file discipline",
                "fanout lanes",
            ),
            "gstack": (
                "garrytan/gstack sprint",
                "`gstack-build`",
                "Gas City fanouts",
            ),
        }
        for pack_name, fragments in pack_expectations.items():
            text = (packs_root / pack_name / "README.md").read_text(encoding="utf-8")
            for fragment in fragments:
                with self.subTest(pack=pack_name, fragment=fragment):
                    self.assertIn(fragment, text)

    def test_build_methodology_assets_do_not_prompt_formula_launch_or_path_skills(self) -> None:
        gascity_root = pathlib.Path(__file__).resolve().parents[1]
        packs_root = gascity_root.parent
        workflow_roots = [
            gascity_root / "assets" / "workflows" / name
            for name in (
                "build-base",
                "build-basic",
                "github-issue-fix-base",
                "implement",
                "same-session-implement",
            )
        ]
        workflow_roots.extend(
            packs_root / pack_name / "assets" / "workflows"
            for pack_name in THIRD_PARTY_BUILD_PACKS
        )
        agent_roots = [
            packs_root / pack_name / "agents"
            for pack_name in THIRD_PARTY_BUILD_PACKS
        ]
        forbidden_fragments = (
            "{{pack_root}}/vendor",
            "/SKILL.md",
            "Launch or reuse",
            "launch or reuse",
            "base `implement` formula",
            "run implement on",
            "run implement until",
            "run the public\ngap-analysis formula",
            "run the generic review workflow",
            "This expansion formula",
            "The expansion formula",
            "formula owns",
            "formula already created",
            "formula expansion is required",
            "formula's child steps",
        )

        paths: list[pathlib.Path] = []
        for root in (*workflow_roots, *agent_roots):
            paths.extend(sorted(root.glob("**/*.md")))

        for path in paths:
            text = path.read_text(encoding="utf-8")
            for fragment in forbidden_fragments:
                with self.subTest(path=path.relative_to(packs_root), fragment=fragment):
                    self.assertNotIn(fragment, text)

    def test_targeted_formulas_consume_graphv2_input_convoy(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        targeted_formulas = {
            "design-review",
            "do-work",
            "do-work-item",
            "fix-convoy",
            "implement",
            "same-session-implement",
        }

        for name in sorted(targeted_formulas):
            with self.subTest(formula=name):
                data = tomllib.loads((root / "formulas" / f"{name}.formula.toml").read_text(encoding="utf-8"))
                self.assertTrue(data.get("target_required"), f"{name} should reject targetless launches")
                self.assertIn("{{convoy_id}}", effective_formula_text(root, name))

    def test_graphv2_formula_text_avoids_legacy_source_workflow_root_key(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]

        for name in FORMULAS:
            with self.subTest(formula=name):
                self.assertNotIn("gc.source_bead_id", effective_formula_text(root, name))

    def test_formula_node_descriptions_delegate_to_shadowable_assets(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        for formula_path in sorted((root / "formulas").glob("*.formula.toml")):
            formula = formula_path.name.removesuffix(".formula.toml")
            data = tomllib.loads(formula_path.read_text(encoding="utf-8"))
            for node in formula_nodes(data):
                with self.subTest(formula=formula, node=node["id"]):
                    self.assertNotIn("description", node)
                    description_file = node.get("description_file")
                    self.assertEqual(
                        description_file,
                        f"../assets/workflows/{formula}/{node['id']}.md",
                    )
                    self.assertTrue((formula_path.parent / description_file).resolve().is_file())

    def test_implement_formula_uses_core_drain_steps(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = tomllib.loads((root / "formulas" / "implement.formula.toml").read_text(encoding="utf-8"))

        self.assertNotIn("infra_target", data["vars"])
        self.assertNotIn("hard_target", data["vars"])
        self.assertNotIn("worker_target", data["vars"])
        self.assertEqual(data["vars"]["implementation_target"]["default"], "gc.implementation-worker")
        self.assertEqual(data["sling_container_mode"], "source")

        step_ids = [step["id"] for step in data["steps"]]
        self.assertEqual(
            step_ids,
            ["prepare", "drain-separate", "drain-same-session", "wait-for-drain", "summarize", "publish"],
        )

        separate = data["steps"][1]
        same = data["steps"][2]
        self.assertEqual(data["steps"][0]["metadata"]["gc.run_target"], "gc.run-operator")
        self.assertEqual(separate["metadata"]["gc.run_target"], "{{implementation_target}}")
        self.assertEqual(separate["condition"], "{{drain_policy}} == separate")
        self.assertEqual(separate["drain"]["context"], "separate")
        self.assertEqual(separate["drain"]["formula"], "do-work")
        self.assertEqual(separate["drain"]["member_access"], "exclusive")
        self.assertEqual(same["metadata"]["gc.run_target"], "{{implementation_target}}")
        self.assertEqual(same["condition"], "{{drain_policy}} == same-session")
        self.assertEqual(same["drain"]["context"], "shared")
        self.assertEqual(same["drain"]["formula"], "do-work-item")
        self.assertEqual(same["drain"]["member_access"], "exclusive")
        self.assertTrue(same["drain"]["item"]["single_lane"])
        self.assertEqual(same["drain"]["on_item_failure"], "skip_remaining")
        self.assertEqual(data["steps"][3]["metadata"]["gc.run_target"], "gc.run-operator")
        self.assertEqual(data["steps"][4]["metadata"]["gc.run_target"], "gc.run-operator")
        self.assertEqual(data["steps"][5]["metadata"]["gc.run_target"], "gc.publisher")
        self.assertEqual(data["steps"][5]["needs"], ["summarize"])
        summarize = node_description(root, data["steps"][4])
        self.assertIn("gc.implementation.summary_path", summarize)
        wait = node_description(root, data["steps"][3])
        for fragment in (
            "Wait only on the core drain control bead",
            "gc.drain_manifest.v1",
            "Do not wait for or inspect downstream steps",
            "summarize, workflow-finalize, or root workflow closure",
            "cannot progress\nuntil this bead closes",
            "close only this wait step",
        ):
            with self.subTest(step="wait-for-drain", fragment=fragment):
                self.assertIn(fragment, wait)
        publish = node_description(root, data["steps"][5])
        for fragment in (
            "push {{push}}",
            "open_pr {{open_pr}}",
            "summary_path {{summary_path}}",
            "publish",
        ):
            with self.subTest(step="publish", fragment=fragment):
                self.assertIn(fragment, publish)

        helper = tomllib.loads((root / "formulas" / "same-session-implement.formula.toml").read_text(encoding="utf-8"))
        self.assertEqual(helper["vars"]["implementation_target"]["default"], "gc.implementation-worker")
        self.assertEqual(helper["steps"][0]["metadata"]["gc.run_target"], "{{implementation_target}}")
        self.assertEqual(helper["steps"][0]["drain"]["formula"], "do-work-item")
        self.assertEqual(helper["steps"][0]["drain"]["member_access"], "exclusive")

    def test_implement_prepare_is_validation_only(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = tomllib.loads((root / "formulas" / "implement.formula.toml").read_text(encoding="utf-8"))
        prepare = next(step for step in data["steps"] if step["id"] == "prepare")

        for fragment in (
            "validation only",
            "Do not edit source files in the launcher checkout",
            "Do not create, modify, or commit source code",
            "Do not run implementation or test-fix loops",
            "CLAIMED_BEAD_ID",
            "gc.root_bead_id",
            "gc.input_convoy_id",
            "validate that input bead is a convoy",
            "do not search repo, plan, report, artifact, session-log, or runtime files",
            "hard-fail if metadata is missing",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, node_description(root, prepare))

    def test_item_implementation_formulas_route_role_agents(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]

        do_work = tomllib.loads((root / "formulas" / "do-work.formula.toml").read_text(encoding="utf-8"))
        self.assertEqual(do_work["extends"], ["implementation-base"])
        self.assertNotIn("infra_target", do_work["vars"])
        self.assertNotIn("hard_target", do_work["vars"])
        self.assertEqual(do_work["vars"]["implementation_target"]["default"], "gc.implementation-worker")
        self.assertEqual(do_work["steps"][0]["metadata"]["gc.run_target"], "gc.run-operator")
        self.assertEqual(do_work["steps"][1]["metadata"]["gc.run_target"], "{{implementation_target}}")
        self.assertEqual(do_work["steps"][2]["metadata"]["gc.run_target"], "gc.run-operator")

        do_work_item = tomllib.loads((root / "formulas" / "do-work-item.formula.toml").read_text(encoding="utf-8"))
        self.assertEqual(do_work_item["extends"], ["implementation-item-base"])
        self.assertNotIn("infra_target", do_work_item["vars"])
        self.assertNotIn("hard_target", do_work_item["vars"])
        self.assertEqual(do_work_item["vars"]["implementation_target"]["default"], "gc.implementation-worker")
        self.assertEqual(do_work_item["steps"][0]["metadata"]["gc.run_target"], "{{implementation_target}}")

    def test_do_work_formula_requires_persisted_item_worktree(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        do_work = tomllib.loads((root / "formulas" / "do-work.formula.toml").read_text(encoding="utf-8"))
        steps = {step["id"]: step for step in do_work["steps"]}

        prepare = node_description(root, steps["prepare-worktree"])
        for fragment in (
            "current step bead metadata",
            "gc.root_bead_id",
            "gc.input_convoy_id",
            "gc.synthetic_kind",
            "gc.drain_member_id",
            "do not use the synthetic drain-unit convoy id as `<source-anchor-id>`",
            "never persist `work_dir` on the synthetic drain-unit convoy",
            "hard-fail if the selected source anchor id equals the synthetic input convoy id",
            "worktrees/<source-anchor-id>",
            "git worktree add",
            "gc bd update <source-anchor-id> --set-metadata work_dir=",
            "Do not edit source files in the launcher checkout",
        ):
            with self.subTest(step="prepare-worktree", fragment=fragment):
                self.assertIn(fragment, prepare)

        implement = node_description(root, steps["implement"])
        for fragment in (
            "Read `work_dir` from the source anchor",
            "never read `work_dir` from the synthetic drain-unit convoy",
            "Do not infer the source anchor from dependency ids",
            "`gc.work_dir` is the launcher rig root, not the implementation worktree",
            "if the JSON output is a one-element list, unwrap the",
            "verify `pwd -P` equals",
            "cd \"$WORKTREE\"",
            "fail this step before editing",
            "Do not edit files in the launcher checkout",
            "Leave the source anchor open",
        ):
            with self.subTest(step="implement", fragment=fragment):
                self.assertIn(fragment, implement)

        close_source = node_description(root, steps["close-source-anchor"])
        for fragment in (
            "Read `work_dir` from the source anchor",
            "close only `<source-anchor-id>`",
            "handle both an object and a",
            "`gc.work_dir` is the launcher rig",
            "does not contain the recorded implementation commit",
            "the city's managed worktree root, not just the",
            "gc bd show <source-anchor-id> --json",
            "status=closed",
            "gc.outcome=pass",
            "if either check fails",
            "anchor before closing this step",
            "Do not close this step with pass while the source anchor remains open",
        ):
            with self.subTest(step="close-source-anchor", fragment=fragment):
                self.assertIn(fragment, close_source)

    def test_close_source_anchor_preserves_evidence_before_reap(self) -> None:
        """Regression coverage for the four durability invariants required
        when preserving implementation evidence before the worktree reaper
        can remove `work_dir` (gcas-bnygo8)."""
        root = pathlib.Path(__file__).resolve().parents[1]
        do_work = tomllib.loads((root / "formulas" / "do-work.formula.toml").read_text(encoding="utf-8"))
        steps = {step["id"]: step for step in do_work["steps"]}
        close_source = node_description(root, steps["close-source-anchor"])

        # (a) copy happens before the source anchor closes.
        copy_pos = close_source.index("Before closing the source anchor, copy any implementation evidence")
        close_pos = close_source.index("On success, close only `<source-anchor-id>`")
        self.assertLess(copy_pos, close_pos)

        # (b) the destination is validated as durable — outside every
        # city-managed worktree, not only the current source anchor's
        # `work_dir` — and the check applies to both the `{{summary_path}}`
        # candidate and the `{{artifact_root}}` fallback.
        for fragment in (
            "Canonicalize the resolved candidate",
            "$GC_CITY/.gc/worktrees/",
            "Apply this same check to the",
            "{{artifact_root}}` fallback itself",
            "fail this step with a configuration error",
        ):
            with self.subTest(invariant="durable-destination", fragment=fragment):
                self.assertIn(fragment, close_source)

        # (c) the primary metadata key is always repointed after a copy.
        self.assertIn(
            'gc bd update <root-bead-id> --set-metadata "gc.implementation.summary_path=<destination>"',
            close_source,
        )

        # (d) the secondary key is repointed only when it also pointed at
        # the worktree-local path.
        self.assertIn(
            "repoint `gc.build.implementation_summary_path` too if it also pointed\n  at the worktree-local path",
            close_source,
        )

    def test_wrapper_formulas_route_role_agents(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]

        issue_fix = resolve_formula(root, "github-issue-fix")
        self.assertNotIn("infra_target", issue_fix["vars"])
        self.assertNotIn("hard_target", issue_fix["vars"])
        route_by_step = {step["id"]: step["metadata"]["gc.run_target"] for step in issue_fix["steps"]}
        self.assertEqual(route_by_step["snapshot"], "gc.run-operator")
        self.assertEqual(route_by_step["triage"], "gc.issue-triager")
        self.assertEqual(route_by_step["triage-gate"], "gc.run-operator")
        self.assertEqual(route_by_step["resume-or-create-run"], "gc.run-operator")
        self.assertEqual(route_by_step["update-status-started"], "gc.run-operator")
        self.assertEqual(route_by_step["generate-requirements"], "gc.requirements-planner")
        self.assertEqual(route_by_step["implementation-plan"], "gc.design-author")
        self.assertEqual(route_by_step["design-review"], "gc.review-synthesizer")
        self.assertEqual(route_by_step["create-beads"], "gc.task-decomposer")
        self.assertEqual(route_by_step["build"], "gc.run-operator")
        self.assertEqual(route_by_step["publish-pr"], "gc.publisher")
        self.assertEqual(route_by_step["finalize"], "gc.run-operator")

        design_review = load_formula(root, "github-issue-fix-design-review-work")
        self.assertEqual(set(design_review.get("vars", {})), {"mode"})
        design_review_text = effective_formula_text(root, "github-issue-fix-design-review-work")
        for target in (
            "gc.run-operator",
            "gc.design-implementation-reviewer",
            "gc.design-test-risk-reviewer",
            "gc.review-synthesizer",
        ):
            with self.subTest(formula="github-issue-fix-design-review-work", target=target):
                self.assertIn(f'"gc.run_target" = "{target}"', design_review_text)
        self.assertNotIn("reviewer_one_target", design_review_text)
        self.assertNotIn("reviewer_two_target", design_review_text)
        self.assertNotIn("synthesizer_target", design_review_text)

    def test_base_formulas_do_not_ship_private_workflow_language(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]

        self.assertFalse((root / "formulas" / "release.formula.toml").exists())
        for path in sorted((root / "formulas").glob("*.formula.toml")):
            raw_text = path.read_text(encoding="utf-8")
            text = raw_text.lower()
            with self.subTest(formula=path.name):
                self.assertNotIn("homebrew", text)
                self.assertNotIn("goreleaser", text)
                self.assertNotIn("gastownhall/gascity", text)
                self.assertNotIn("bugflow", text)
                self.assertNotIn("Ralph", raw_text)
                self.assertNotIn(".ralph", text)

    def test_report_formulas_are_targetless_and_report_only(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        for name in ("gap-analysis", "review"):
            data = tomllib.loads((root / "formulas" / f"{name}.formula.toml").read_text(encoding="utf-8"))
            self.assertEqual(data["mode"], "report")
            self.assertFalse(data["target_required"])
            self.assertEqual([step["id"] for step in data["steps"]], ["validate-context", "write-report"])

    def test_github_adapter_formulas_are_targetless_url_adapters(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        expected = {
            "github-issue-triage": ("github_issue_url", {"artifact_root", "post_mode", "triage_rubric_path"}),
            "github-pr-review": (
                "github_pr_url",
                {
                    "artifact_root",
                    "code_review_formula",
                    "context_path",
                    "interaction_mode",
                    "review_mode",
                    "post_mode",
                },
            ),
            "github-issue-fix": (
                "github_issue_url",
                {
                    "artifact_root",
                    "code_review_formula",
                    "decomposition_formula",
                    "mode",
                    "interaction_mode",
                    "review_mode",
                    "implementation_formula",
                    "implementation_item_formula",
                    "pr_mode",
                    "planning_formula",
                    "drain_policy",
                    "implementation_target",
                    "review_fix_formula",
                },
            ),
        }
        for name, (url_var, optional_vars) in expected.items():
            with self.subTest(name=name):
                data = resolve_formula(root, name)
                self.assertEqual(data["requires"]["formula_compiler"], ">=2.0.0")
                self.assertFalse(data["target_required"])
                self.assertTrue(data["vars"][url_var]["required"])
                self.assertEqual(set(data["vars"]) - {url_var}, optional_vars)
                text = effective_formula_text(root, name)
                self.assertIn("{{pack_root}}/assets/scripts/github_api.py", text)
                self.assertNotIn("{{pack_root}}" + "/scripts/", text)

    def test_github_adapter_formulas_define_source_bead_contract(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        expected = {
            "github-issue-triage": ("issue", "gc.github.body_hash"),
            "github-issue-fix": ("issue", "gc.github.body_hash"),
            "github-pr-review": ("pull", "gc.github.head_sha"),
        }
        required_common = {
            "gc bd list --metadata-field gc.kind=github_source",
            "gc bd create",
            "gc bd update",
            "--external-ref",
            "gc.github.kind",
            "gc.github.repo",
            "gc.github.number",
            "gc.github.url",
            "gc.github.snapshot_path",
            "Do not route the source bead",
        }

        for name, (github_kind, idempotency_key) in expected.items():
            with self.subTest(name=name):
                text = effective_formula_text(root, name)
                for fragment in required_common:
                    self.assertIn(fragment, text)
                self.assertIn(f"gc.github.kind={github_kind}", text)
                self.assertIn(idempotency_key, text)

    def test_github_adapter_formulas_define_artifact_root_semantics(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        for name in ("github-issue-triage", "github-issue-fix", "github-pr-review"):
            with self.subTest(name=name):
                text = effective_formula_text(root, name)
                self.assertIn("{{pack_root}}/assets/scripts/artifacts.py root", text)
                self.assertIn("{{pack_root}}/assets/scripts/artifacts.py path", text)
                self.assertIn("artifact-root-relative", text)
                self.assertIn("not filesystem-root absolute", text)
                self.assertIn("gc.github.snapshot_path=<absolute source.json path>", text)

    def test_github_pr_review_delegates_with_explicit_review_artifacts(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = resolve_formula(root, "github-pr-review")
        text = effective_formula_text(root, "github-pr-review")
        reuse_current = node_description(root, next(step for step in data["steps"] if step["id"] == "reuse-current-head"))
        run_review = node_description(root, next(step for step in data["steps"] if step["id"] == "run-review"))
        render_comment = node_description(root, next(step for step in data["steps"] if step["id"] == "render-comment"))

        for fragment in (
            "gc.github.review_dir=<absolute review directory>",
            "gc.github.review_subject_path",
            "gc.github.review_report_path",
            "gc.github.comment_path",
            "gc.github.review_outcome",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)
        for fragment in (
            "gc.github.reused_current_output=true",
            "gc.github.reused_current_output=false",
            "gc.github.review_report_path",
            "gc.github.comment_path",
        ):
            with self.subTest(step="reuse-current-head", fragment=fragment):
                self.assertIn(fragment, reuse_current)
        for fragment in (
            "SUBJECT_PATH=<gc.github.review_dir>/subject.md",
            "REPORT_PATH=<gc.github.review_dir>/review-report.md",
            "gc sling gc.run-operator {{code_review_formula}} --formula",
            "--var subject_path=\"$SUBJECT_PATH\"",
            "--var report_path=\"$REPORT_PATH\"",
            "review-outcome \"$REPORT_PATH\"",
            "gc.github.reused_current_output=true",
            "do not\nlaunch the generic `review` formula",
            "leave the reused\nartifacts untouched",
        ):
            with self.subTest(step="run-review", fragment=fragment):
                self.assertIn(fragment, run_review)
        for fragment in (
            "<gc.github.review_dir>/comment.md",
            "gc.github.reused_current_output=true",
            "do not\nrewrite the rendered comment",
            "real no-op path",
        ):
            with self.subTest(step="render-comment", fragment=fragment):
                self.assertIn(fragment, render_comment)

    def test_github_issue_fix_uses_implementation_plan_artifact_contract(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        text = effective_formula_text(root, "github-issue-fix")

        self.assertIn("implementation-plan.md", text)
        self.assertIn("implementation_plan_file", text)
        self.assertIn("create beads", text.lower())
        self.assertNotIn("design_file", text)

    def test_github_issue_fix_run_setup_publishes_plan_artifact_metadata(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = resolve_formula(root, "github-issue-fix")
        steps = {step["id"]: step for step in data["steps"]}

        setup = node_description(root, steps["resume-or-create-run"])
        requirements = node_description(root, steps["generate-requirements"])
        implementation_plan = node_description(root, steps["implementation-plan"])
        create_beads = node_description(root, steps["create-beads"])
        publish_pr = node_description(root, steps["publish-pr"])
        requirements_normalized = " ".join(requirements.split())
        implementation_plan_normalized = " ".join(implementation_plan.split())

        for fragment in (
            "gc bd update <root-bead-id>",
            "gc.github.run_dir",
            "gc.github.requirements_path",
            "gc.github.implementation_plan_path",
            "gc.github.design_path",
            "absolute path",
        ):
            with self.subTest(step="resume-or-create-run", fragment=fragment):
                self.assertIn(fragment, setup)
        for fragment in (
            "gc.github.requirements_path",
            "different path",
        ):
            with self.subTest(step="generate-requirements", fragment=fragment):
                self.assertIn(fragment, requirements_normalized)
        self.assertIn("Do not choose or invent", requirements)
        for fragment in (
            "gc.github.implementation_plan_path",
            "different path",
        ):
            with self.subTest(step="implementation-plan", fragment=fragment):
                self.assertIn(fragment, implementation_plan_normalized)
        self.assertIn("Do not choose or invent", implementation_plan)
        for step_name, text in (
            ("resume-or-create-run", setup),
            ("implementation-plan", implementation_plan),
            ("create-beads", create_beads),
            ("publish-pr", publish_pr),
        ):
            for fragment in (
                "passive wait + mail",
                "gc session wait",
                "gc mail send human",
                "mail_sent=true",
                "silence",
            ):
                with self.subTest(step=step_name, fragment=fragment):
                    self.assertIn(fragment, text)

    def test_github_issue_fix_reviews_implementation_plan_without_design_alias_step(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = resolve_formula(root, "github-issue-fix")
        steps = {step["id"]: step for step in data["steps"]}
        step_ids = [step["id"] for step in data["steps"]]

        self.assertNotIn("design", steps)
        self.assertLess(step_ids.index("implementation-plan"), step_ids.index("design-review"))
        self.assertEqual(steps["design-review"]["needs"], ["implementation-plan"])
        self.assertFalse((root / "assets" / "workflows" / "github-issue-fix-base" / "design.md").exists())

    def test_layered_github_issue_overrides_preserve_catalog_and_resolve(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            override_dir = pathlib.Path(tmp)
            (override_dir / "github-issue-fix.formula.toml").write_text(
                """
formula = "github-issue-fix"
extends = ["github-issue-fix-base"]
version = 1
contract = "graph.v2"
target_required = false

[catalog]
name = "github-issue-fix"
description = "Fix a GitHub issue with a local advanced design-review override."

[[steps]]
id = "design-review"
title = "Run local advanced design review"
needs = ["implementation-plan"]
metadata = { "gc.run_target" = "gc.review-synthesizer" }
description = "Override sink that preserves the base issue-fix protocol."
""".lstrip(),
                encoding="utf-8",
            )
            (override_dir / "github-issue-triage.formula.toml").write_text(
                """
formula = "github-issue-triage"
extends = ["github-issue-triage-base"]
version = 1
contract = "graph.v2"
target_required = false

[catalog]
name = "github-issue-triage"
description = "Triage a GitHub issue with a local triage-work override."

[[steps]]
id = "write-triage-report"
title = "Run local issue triage"
needs = ["reuse-current-body-hash"]
metadata = { "gc.run_target" = "gc.issue-triager" }
description = "Override sink that writes the base triage report contract."
""".lstrip(),
                encoding="utf-8",
            )

            layered_dirs = [root / "formulas", override_dir]
            issue_fix = resolve_formula_from_dirs(layered_dirs, "github-issue-fix")
            issue_triage = resolve_formula_from_dirs(layered_dirs, "github-issue-triage")

            self.assertEqual(load_formula_from_dirs(layered_dirs, "github-issue-fix")["catalog"]["name"], "github-issue-fix")
            self.assertEqual(
                load_formula_from_dirs(layered_dirs, "github-issue-triage")["catalog"]["name"],
                "github-issue-triage",
            )
            self.assertEqual(
                next(step for step in issue_fix["steps"] if step["id"] == "design-review")["needs"],
                ["implementation-plan"],
            )
            for data in (issue_fix, issue_triage):
                step_ids = {step["id"] for step in data["steps"]}
                for step in data["steps"]:
                    for need in step.get("needs", []):
                        with self.subTest(formula=data["formula"], step=step["id"], need=need):
                            self.assertIn(need, step_ids)

    def test_github_issue_triage_formula_requires_human_readable_analysis(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        text = effective_formula_text(root, "github-issue-triage")
        self.assertIn("human-readable analysis body", text)
        self.assertIn("## Summary", text)
        self.assertIn("## Evidence", text)
        self.assertIn("## Recommendation", text)
        self.assertIn("render-triage-comment", text)

    def test_github_issue_triage_uses_workflow_metadata_as_context_index(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        text = effective_formula_text(root, "github-issue-triage")

        required_fragments = {
            "workflow root metadata",
            "gc.root_bead_id",
            "gc.github.source_bead_id",
            "gc.github.triage_dir",
            "gc bd show <root-bead-id> --json",
            "gc bd update <root-bead-id>",
            "Read `gc.github.snapshot_path`",
            "Do not write a separate triage context file",
        }
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)
        self.assertNotIn("triage-context.json", text)

    def test_github_issue_triage_reuse_path_noops_downstream_steps(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = resolve_formula(root, "github-issue-triage")
        reuse_current = node_description(root, next(step for step in data["steps"] if step["id"] == "reuse-current-body-hash"))
        write_report = node_description(root, next(step for step in data["steps"] if step["id"] == "write-triage-report"))
        render_comment = node_description(root, next(step for step in data["steps"] if step["id"] == "render-comment"))

        for fragment in (
            "gc.github.reused_current_output=true",
            "gc.github.reused_current_output=false",
            "gc.github.triage_report_path",
            "gc.github.comment_path",
        ):
            with self.subTest(step="reuse-current-body-hash", fragment=fragment):
                self.assertIn(fragment, reuse_current)
        for fragment in (
            "gc.github.reused_current_output=true",
            "do not\n  investigate or rewrite `triage-report.md`",
            "leave the reused artifacts\n  untouched",
        ):
            with self.subTest(step="write-triage-report", fragment=fragment):
                self.assertIn(fragment, write_report)
        for fragment in (
            "gc.github.reused_current_output=true",
            "do not rewrite the rendered comment",
            "real no-op path",
        ):
            with self.subTest(step="render-comment", fragment=fragment):
                self.assertIn(fragment, render_comment)

    def test_github_issue_triage_snapshot_creates_triage_directory(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = resolve_formula(root, "github-issue-triage")
        snapshot = node_description(root, next(step for step in data["steps"] if step["id"] == "snapshot"))

        self.assertIn(
            '--relative "/github/issues/<owner>/<repo>/<number>/triage/<body-hash>/" --directory --mkdir-parents',
            snapshot,
        )

    def test_github_issue_triage_supports_rubric_override_without_protocol_override(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = resolve_formula(root, "github-issue-triage")
        text = effective_formula_text(root, "github-issue-triage")

        self.assertIn("triage_rubric_path", data["vars"])
        self.assertEqual(data["vars"]["triage_rubric_path"]["default"], "")
        self.assertIn("{{triage_rubric_path}}", text)
        self.assertIn("Optional rubric/prompt override path", text)
        self.assertIn("report behavior, not the metadata protocol", text)
        self.assertIn("must not override", text)
        self.assertIn("gc.github-issue-triage-report.v1", text)

    def test_github_issue_triage_human_gate_uses_runtime_metadata_in_step_body(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        data = resolve_formula(root, "github-issue-triage")

        gate = next(step for step in data["steps"] if step["id"] == "human-gate-sensitive-output")
        self.assertNotIn("condition", gate)
        self.assertIn("gc.github.triage_priority", node_description(root, gate))
        self.assertIn("no-op gate", node_description(root, gate))
        self.assertIn("gc.github.public_comment_gate", node_description(root, gate))

    def test_github_public_comment_post_steps_enforce_gate_contract(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        pr_review = resolve_formula(root, "github-pr-review")
        issue_triage = resolve_formula(root, "github-issue-triage")

        pr_gate = next(step for step in pr_review["steps"] if step["id"] == "human-gate-comment")
        self.assertNotIn("condition", pr_gate)
        issue_gate = next(step for step in issue_triage["steps"] if step["id"] == "human-gate-sensitive-output")

        checks = (
            ("github-pr-review gate", node_description(root, pr_gate)),
            (
                "github-pr-review post",
                node_description(root, next(step for step in pr_review["steps"] if step["id"] == "post-comment")),
            ),
            (
                "github-issue-triage gate",
                node_description(root, issue_gate),
            ),
            (
                "github-issue-triage post",
                node_description(root, next(step for step in issue_triage["steps"] if step["id"] == "post-comment")),
            ),
        )
        for label, text in checks:
            for fragment in (
                "gc.github.public_comment_gate",
                "approved",
                "not_required",
                "rejected",
                "revision_requested",
            ):
                with self.subTest(label=label, fragment=fragment):
                    self.assertIn(fragment, text)

        for label, text in (
            ("github-pr-review gate", node_description(root, pr_gate)),
            ("github-issue-triage gate", node_description(root, issue_gate)),
        ):
            for fragment in (
                "passive wait + mail",
                "gc session wait",
                "gc mail send human",
                "mail_sent=true",
                "silence",
            ):
                with self.subTest(label=label, fragment=fragment):
                    self.assertIn(fragment, text)

    def test_all_declared_formula_vars_are_rendered_into_graph_text(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        for path in sorted((root / "formulas").glob("*.formula.toml")):
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            text = effective_formula_text(root, path.name.removesuffix(".formula.toml"))
            for var_name in data.get("vars", {}):
                with self.subTest(formula=path.name, var=var_name):
                    if data.get("type") == "expansion":
                        self.assertTrue(
                            f"{{{{{var_name}}}}}" in text or f"{{{var_name}}}" in text,
                            f"{path.name} must render {var_name} as a runtime or expansion variable",
                        )
                    else:
                        self.assertIn(f"{{{{{var_name}}}}}", text)

    def test_check_scripts_are_executable_and_portable(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        scripts = sorted((root / "assets" / "scripts" / "checks").glob("*.sh"))

        self.assertEqual(
            [script.name for script in scripts],
            [
                "build-artifact-valid.sh",
                "design-review-approved.sh",
                "gap-analysis-approved.sh",
                "implementation-handoff-valid.sh",
                "implementation-review-approved.sh",
                "pr-ci-green.sh",
                "preflight-evidence-valid.sh",
            ],
        )
        for script in scripts:
            text = script.read_text(encoding="utf-8")
            self.assertTrue(os.access(script, os.X_OK), f"{script} must be executable")
            self.assertNotIn("/data/projects", text)
            self.assertNotIn("gascity-packs-worktrees", text)

    def test_producer_stages_gate_artifacts_with_bounded_repair(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]

        for (formula_name, step_id), (schema, path_keys) in BUILD_ARTIFACT_VALIDATION_GATES.items():
            with self.subTest(formula=formula_name, step=step_id):
                formula = load_formula(root, formula_name)
                nodes = formula.get("steps") or formula.get("template") or []
                nodes_by_id = {node["id"]: node for node in nodes}
                self.assertIn(step_id, nodes_by_id, f"{formula_name} lost producer node {step_id}")
                step = nodes_by_id[step_id]

                self.assertIn(
                    "check",
                    step,
                    f"{formula_name}.{step_id} lost its build-artifact validation gate",
                )
                self.assertEqual(
                    step["check"]["max_attempts"],
                    BUILD_ARTIFACT_GATE_MAX_ATTEMPTS,
                    f"{formula_name}.{step_id} must keep one produce plus two bounded repair attempts",
                )
                check_script, check_timeout = BUILD_ARTIFACT_GATE_CHECK_OVERRIDES.get(
                    (formula_name, step_id),
                    (BUILD_ARTIFACT_CHECK_SCRIPT, "5m"),
                )
                self.assertEqual(
                    step["check"]["check"],
                    {
                        "mode": "exec",
                        "path": check_script,
                        "timeout": check_timeout,
                    },
                )
                self.assertEqual(step["metadata"]["gc.build.artifact_schema"], schema)
                self.assertEqual(step["metadata"]["gc.build.artifact_path_keys"], path_keys)

    def _run_build_artifact_check(
        self,
        beads_by_id: dict[str, str],
        bead_id: str,
        extra_env: dict[str, str] | None = None,
        script_root: pathlib.Path | None = None,
        bead_errors: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        """bead_errors: extra files for the fake store, `<id>.error` (what
        `gc bd show <id>` prints when it fails) and `<id>.flaky` (how many
        times it fails before it answers)."""
        root = pathlib.Path(__file__).resolve().parents[1]
        script = root / "assets" / "scripts" / "checks" / "build-artifact-valid.sh"

        if script_root is not None:
            installed_check_dir = script_root / ".gc" / "scripts" / "checks"
            installed_check_dir.mkdir(parents=True)
            installed_script = installed_check_dir / script.name
            installed_script.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
            installed_script.chmod(0o755)
            validator = root / "assets" / "scripts" / "validate_build_artifact.py"
            (installed_check_dir.parent / validator.name).write_text(
                validator.read_text(encoding="utf-8"), encoding="utf-8"
            )
            script = installed_script

        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            show_dir = tmp / "show"
            show_dir.mkdir()
            for bead, payload in beads_by_id.items():
                (show_dir / f"{bead}.json").write_text(payload, encoding="utf-8")
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "if [ \"${1:-}\" = \"formula\" ] && [ \"${2:-}\" = \"list\" ]; then\n"
                "  [ -n \"${FORMULA_LIST_JSON:-}\" ] || exit 2\n"
                "  cat \"$FORMULA_LIST_JSON\"\n"
                "  exit 0\n"
                "fi\n"
                "while [ \"${1:-}\" != \"bd\" ]; do [ $# -gt 0 ] || exit 2; shift; done\n"
                "shift\n"
                "case \"$1\" in\n"
                "  version) exit 0 ;;\n"
                "  show)\n"
                "    if [ -f \"$BD_SHOW_DIR/$2.flaky\" ] && [ \"$(cat \"$BD_SHOW_DIR/$2.flaky\")\" -gt 0 ]; then\n"
                "      echo $(( $(cat \"$BD_SHOW_DIR/$2.flaky\") - 1 )) >\"$BD_SHOW_DIR/$2.flaky\"\n"
                "      cat \"$BD_SHOW_DIR/$2.error\" >&2; exit 1\n"
                "    fi\n"
                "    if [ -f \"$BD_SHOW_DIR/$2.json\" ]; then cat \"$BD_SHOW_DIR/$2.json\"; exit 0; fi\n"
                "    if [ -f \"$BD_SHOW_DIR/$2.error\" ]; then cat \"$BD_SHOW_DIR/$2.error\" >&2; exit 1; fi\n"
                "    echo \"Error fetching $2: no issue found matching \\\"$2\\\"\" >&2; exit 1 ;;\n"
                "  *) exit 2 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_gc.chmod(0o755)
            for name, text in (bead_errors or {}).items():
                (show_dir / name).write_text(text, encoding="utf-8")

            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "BD_SHOW_DIR": str(show_dir),
                "GC_BEAD_ID": bead_id,
                "BUILD_ARTIFACT_RETRY_SLEEP_SECONDS": "0.05",
                "BUILD_ARTIFACT_INFRA_BUDGET_SECONDS": "30",
                **(extra_env or {}),
            }
            return subprocess.run(
                [str(script)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

    def _run_implementation_review_check(
        self,
        *,
        show_json: str,
        list_json: str,
        parent_show_json: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        root = pathlib.Path(__file__).resolve().parents[1]
        script = root / "assets" / "scripts" / "checks" / "implementation-review-approved.sh"

        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            show_path = tmp / "show.json"
            parent_show_path = tmp / "parent-show.json"
            list_path = tmp / "list.json"
            show_path.write_text(show_json, encoding="utf-8")
            parent_show_path.write_text(parent_show_json or show_json, encoding="utf-8")
            list_path.write_text(list_json, encoding="utf-8")
            write_check_gc_stub(bin_dir, parent_show=True)

            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "BD_SHOW_JSON": str(show_path),
                "BD_PARENT_SHOW_JSON": str(parent_show_path),
                "BD_LIST_JSON": str(list_path),
                "GC_BEAD_ID": "loop",
                "GC_ITERATION": "1",
                **(extra_env or {}),
            }
            return subprocess.run(
                [str(script)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_implementation_review_check_accepts_approved_build_basic_lanes(self) -> None:
        show_json = """[
  {
    "id": "loop",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.step_id": "review.build-basic-review-loop",
      "gc.step_ref": "build-basic.review.build-basic-review-loop"
    }
  }
]"""
        list_json = """[
  {
    "id": "acceptance",
    "updated_at": "2026-06-15T01:00:00Z",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "review.build-basic-review-loop",
      "gc.scope_ref": "review.build-basic-review-loop.iteration.1",
      "code_review.acceptance_verdict": "approve"
    }
  },
  {
    "id": "test-evidence",
    "updated_at": "2026-06-15T01:00:01Z",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "review.build-basic-review-loop",
      "gc.scope_ref": "review.build-basic-review-loop.iteration.1",
      "code_review.test_evidence_verdict": "approve"
    }
  },
  {
    "id": "simplicity",
    "updated_at": "2026-06-15T01:00:02Z",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "review.build-basic-review-loop",
      "gc.scope_ref": "review.build-basic-review-loop.iteration.1",
      "code_review.simplicity_verdict": "approve"
    }
  },
  {
    "id": "preflight",
    "updated_at": "2026-06-15T01:00:03Z",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "review.build-basic-review-loop",
      "gc.scope_ref": "review.build-basic-review-loop.iteration.1",
      "code_review.preflight_verdict": "approve"
    }
  }
]"""

        result = self._run_implementation_review_check(show_json=show_json, list_json=list_json)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Implementation review approved from lane verdicts", result.stdout)

    def test_implementation_review_check_accepts_resolved_critical_findings(self) -> None:
        show_json = """[
  {
    "id": "loop",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.step_id": "superpowers-review.write-report.superpowers-code-review-loop"
    }
  }
]"""
        list_json = """[
  {
    "id": "review-fixes",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "superpowers-review.write-report.superpowers-code-review-loop",
      "code_review.verdict": "done",
      "code_review.report_path": "review-fix-summary.md"
    }
  }
]"""

        with tempfile.TemporaryDirectory() as td:
            work_dir = pathlib.Path(td)
            (work_dir / "review-fix-summary.md").write_text(
                "# Review Fix Summary\n\n"
                "## Findings\n\n"
                "### [ALREADY RESOLVED] R-001\n\n"
                "**Severity**: Critical\n\n"
                "The critical command-injection finding was fixed and verified.\n",
                encoding="utf-8",
            )

            result = self._run_implementation_review_check(
                show_json=show_json,
                list_json=list_json,
                extra_env={"GC_WORK_DIR": str(work_dir)},
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Implementation review approved", result.stdout)

    def test_implementation_review_check_accepts_report_mode_with_report_path(self) -> None:
        show_json = """[
  {
    "id": "loop",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.step_id": "write-report.bmad-code-review-loop"
    }
  }
]"""
        parent_show_json = """[
  {
    "id": "root",
    "metadata": {
      "gc.var.review_mode": "report",
      "gc.build.code_review_report_path": ".gc/inference-gate/code-review/review-report.md"
    }
  }
]"""
        list_json = "[]"

        result = self._run_implementation_review_check(
            show_json=show_json,
            parent_show_json=parent_show_json,
            list_json=list_json,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Implementation review report mode satisfied", result.stdout)

    def test_methodology_code_review_expansions_are_report_mode_aware(self) -> None:
        repo = pathlib.Path(__file__).resolve().parents[2]
        cases = {
            "bmad": {
                "pack_dir": repo / "bmad",
                "review_formula": "bmad-review",
                "build_formula": "bmad-build",
                "expansion": "bmad-code-review-flow",
                "fix_child": "{target}.apply-bmad-review-findings",
                "synthesis": "bmad-code-review-flow/{target}.synthesize-bmad-review.md",
                "finalize": "bmad-code-review-flow/{target}.md",
            },
            "compound-engineering": {
                "pack_dir": repo / "compound-engineering",
                "review_formula": "compound-review",
                "build_formula": "compound-build",
                "expansion": "compound-code-review",
                "fix_child": "{target}.apply-review-findings",
                "synthesis": "compound-code-review/{target}.synthesize-code-review.md",
                "finalize": "compound-code-review/{target}.md",
            },
            "gstack": {
                "pack_dir": repo / "gstack",
                "review_formula": "gstack-review",
                "build_formula": "gstack-build",
                "expansion": "gstack-code-review",
                "fix_child": "{target}.apply-review-findings",
                "synthesis": "gstack-code-review/{target}.synthesize-code-review.md",
                "finalize": "gstack-code-review/{target}.md",
            },
            "superpowers": {
                "pack_dir": repo / "superpowers",
                "review_formula": "superpowers-review",
                "build_formula": "superpowers-build",
                "expansion": "superpowers-code-review",
                "fix_child": "{target}.process-code-review",
                "synthesis": None,
                "finalize": "superpowers-code-review/{target}.md",
            },
        }

        def expanded_steps(formula_data: dict) -> list[dict]:
            return [
                step
                for step in formula_data.get("steps", [])
                if step.get("expand") in {case["expansion"] for case in cases.values()}
            ]

        def child_by_id(formula_data: dict, child_id: str) -> dict:
            for template in formula_data.get("template", []):
                for child in template.get("children", []):
                    if child.get("id") == child_id:
                        return child
            raise AssertionError(f"missing child {child_id}")

        for pack_name, case in cases.items():
            pack_dir = case["pack_dir"]
            expansion_data = tomllib.loads(
                (pack_dir / "formulas" / f"{case['expansion']}.formula.toml").read_text(encoding="utf-8")
            )
            with self.subTest(pack=pack_name, check="expansion-var"):
                self.assertIn("review_mode", expansion_data.get("vars", {}))
                self.assertEqual(
                    expansion_data.get("vars", {}).get("artifact_path_keys", {}).get("default"),
                    "gc.build.code_review_report_path,gc.build.review_report_path,gc.var.report_path",
                )
                self.assertEqual(
                    child_by_id(expansion_data, case["fix_child"]).get("condition"),
                    "{{review_mode}} != report",
                )

            for formula_name in (case["review_formula"], case["build_formula"]):
                formula_data = tomllib.loads(
                    (pack_dir / "formulas" / f"{formula_name}.formula.toml").read_text(encoding="utf-8")
                )
                matching_steps = expanded_steps(formula_data)
                self.assertTrue(matching_steps, f"{pack_name}/{formula_name} has no review expansion")
                for step in matching_steps:
                    with self.subTest(pack=pack_name, formula=formula_name, step=step["id"]):
                        self.assertEqual(step["expand_vars"].get("review_mode"), "{{review_mode}}")

            finalize_text = (pack_dir / "assets" / "workflows" / case["finalize"]).read_text(encoding="utf-8")
            with self.subTest(pack=pack_name, check="finalize-report-mode"):
                self.assertIn("gc.var.review_mode=report", finalize_text)
                self.assertIn("code_review.verdict=reported", finalize_text)

            if case["synthesis"]:
                synthesis_text = (pack_dir / "assets" / "workflows" / case["synthesis"]).read_text(
                    encoding="utf-8"
                )
                with self.subTest(pack=pack_name, check="synthesis-schema"):
                    self.assertIn("schema: gc.build.review.v1", synthesis_text)
                    self.assertIn("workflow:\n", synthesis_text)
                    self.assertIn("methodology:\n", synthesis_text)
                    self.assertIn("producer:\n", synthesis_text)
                    self.assertIn("trace:\n", synthesis_text)
                    self.assertIn("upstream:\n", synthesis_text)
                    self.assertIn("coverage:\n", synthesis_text)
                    self.assertIn("status: changes_required", synthesis_text)
                    self.assertIn("Do not use dotted YAML keys", synthesis_text)
                    self.assertIn("do not make `trace` a list", synthesis_text)
                    self.assertIn("`ID` and `Status` columns", synthesis_text)

        context_prompt = (repo / "gascity" / "assets" / "workflows" / "code-review-base" / "validate-context.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("rendered values in this prompt are authoritative", context_prompt)
        self.assertIn("interaction_mode: `{{interaction_mode}}`", context_prompt)
        self.assertIn("review_mode: `{{review_mode}}`", context_prompt)
        self.assertIn("Do not require the `report_path` file to exist before review", context_prompt)
        self.assertIn("Do not require", context_prompt)
        self.assertIn("review-config.yaml", context_prompt)

    def test_implementation_review_check_rejects_incomplete_build_basic_lanes(self) -> None:
        show_json = """[
  {
    "id": "loop",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.step_id": "review.build-basic-review-loop"
    }
  }
]"""
        list_json = """[
  {
    "id": "acceptance",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "review.build-basic-review-loop",
      "code_review.acceptance_verdict": "approve"
    }
  },
  {
    "id": "test-evidence",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "review.build-basic-review-loop",
      "code_review.test_evidence_verdict": "iterate"
    }
  },
  {
    "id": "simplicity",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "review.build-basic-review-loop",
      "code_review.simplicity_verdict": "approve"
    }
  }
]"""

        result = self._run_implementation_review_check(show_json=show_json, list_json=list_json)

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Implementation review needs another iteration", result.stdout)
        self.assertIn("test_evidence=iterate", result.stdout)

    @staticmethod
    def _valid_requirements_artifact() -> str:
        sections = []
        for section in (
            "Problem Statement",
            "W6H",
            "User Stories",
            "Technical Stories",
            "Behavior Requirements",
            "Example Mapping",
            "Acceptance Criteria",
            "Out Of Scope",
            "Open Questions",
        ):
            content = f"{section} content."
            if section == "Example Mapping":
                content += (
                    "\n\n| ID | Status |\n"
                    "| --- | --- |\n"
                    "| GC-METH-001 | covered |"
                )
            sections.append(f"## {section}\n\n{content}")
        body = "\n\n".join(sections)
        return (
            "---\n"
            "schema: gc.build.requirements.v1\n"
            "workflow:\n"
            "  id: build-20260610-001\n"
            "  formula: build-basic\n"
            "methodology:\n"
            "  pack: gascity\n"
            "  name: build-basic\n"
            "producer:\n"
            "  formula: planning-base\n"
            "  stage: requirements\n"
            "  attempt: 1\n"
            "status: approved\n"
            "trace:\n"
            "  upstream:\n"
            "    - path: request.md\n"
            "      hash: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            "  coverage:\n"
            "    - id: GC-METH-001\n"
            "      status: covered\n"
            "---\n"
            "\n"
            f"{body}\n"
        )

    def test_build_artifact_check_passes_valid_recorded_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as artifact_dir:
            artifact = pathlib.Path(artifact_dir) / "requirements.md"
            artifact.write_text(self._valid_requirements_artifact(), encoding="utf-8")

            control = (
                '[{"id": "loop", "metadata": {'
                '"gc.root_bead_id": "root", '
                '"gc.build.artifact_schema": "gc.build.requirements.v1", '
                '"gc.build.artifact_path_keys": "gc.build.requirements_path,gc.var.requirements_path"}}]'
            )
            root_bead = (
                '[{"id": "root", "metadata": {'
                f'"gc.build.requirements_path": "{artifact}"'
                "}}]"
            )
            result = self._run_build_artifact_check(
                {"loop": control, "root": root_bead}, "loop"
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("build artifact valid", result.stdout)

    def test_build_artifact_check_resolves_relative_path_from_rig_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            rig_root = tmp / "rig"
            artifact = rig_root / ".gc" / "inference-gate" / "requirements.md"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(self._valid_requirements_artifact(), encoding="utf-8")
            per_bead_worktree = tmp / "per-bead-worktree"
            per_bead_worktree.mkdir()

            control = (
                '[{"id": "loop", "metadata": {'
                '"gc.root_bead_id": "root", '
                '"gc.build.artifact_schema": "gc.build.requirements.v1", '
                '"gc.build.artifact_path_keys": "gc.build.requirements_path"}}]'
            )
            root_bead = (
                '[{"id": "root", "metadata": {'
                '"gc.build.requirements_path": ".gc/inference-gate/requirements.md"'
                '}}]'
            )
            result = self._run_build_artifact_check(
                {"loop": control, "root": root_bead},
                "loop",
                extra_env={
                    "GC_RIG_ROOT": str(rig_root),
                    "GC_WORK_DIR": str(per_bead_worktree),
                },
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(str(artifact), result.stdout)

    def test_build_artifact_check_uses_beads_scope_root_when_rig_root_is_unset(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            rig_root = tmp / "rig"
            artifact = rig_root / ".gc" / "inference-gate" / "requirements.md"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(self._valid_requirements_artifact(), encoding="utf-8")
            per_bead_worktree = tmp / "per-bead-worktree"
            per_bead_worktree.mkdir()

            control = (
                '[{"id": "loop", "metadata": {'
                '"gc.root_bead_id": "root", '
                '"gc.build.artifact_schema": "gc.build.requirements.v1", '
                '"gc.build.artifact_path_keys": "gc.build.requirements_path"}}]'
            )
            root_bead = (
                '[{"id": "root", "metadata": {'
                '"gc.build.requirements_path": ".gc/inference-gate/requirements.md"'
                '}}]'
            )
            result = self._run_build_artifact_check(
                {"loop": control, "root": root_bead},
                "loop",
                extra_env={
                    "GC_RIG_ROOT": "",
                    "GC_BEADS_SCOPE_ROOT": str(rig_root),
                    "GC_WORK_DIR": str(per_bead_worktree),
                },
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(str(artifact), result.stdout)

    def test_build_artifact_check_derives_rig_root_from_installed_script_when_env_is_unset(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            rig_root = tmp / "rig"
            artifact = rig_root / ".gc" / "inference-gate" / "requirements.md"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(self._valid_requirements_artifact(), encoding="utf-8")
            per_bead_worktree = tmp / "per-bead-worktree"
            per_bead_worktree.mkdir()
            source_root = pathlib.Path(__file__).resolve().parents[1]
            validator = source_root / "assets" / "scripts" / "validate_build_artifact.py"
            worktree_validator = per_bead_worktree / "gascity" / "assets" / "scripts" / validator.name
            worktree_validator.parent.mkdir(parents=True)
            worktree_validator.write_text(validator.read_text(encoding="utf-8"), encoding="utf-8")
            for schema in (source_root / "schemas" / "build").glob("*.yaml"):
                destination = per_bead_worktree / "gascity" / "schemas" / "build" / schema.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(schema.read_text(encoding="utf-8"), encoding="utf-8")

            control = (
                '[{"id": "loop", "metadata": {'
                '"gc.root_bead_id": "root", '
                '"gc.build.artifact_schema": "gc.build.requirements.v1", '
                '"gc.build.artifact_path_keys": "gc.build.requirements_path"}}]'
            )
            root_bead = (
                '[{"id": "root", "metadata": {'
                '"gc.build.requirements_path": ".gc/inference-gate/requirements.md"'
                '}}]'
            )
            result = self._run_build_artifact_check(
                {"loop": control, "root": root_bead},
                "loop",
                extra_env={
                    "GC_RIG_ROOT": "",
                    "GC_BEADS_SCOPE_ROOT": "",
                    "GC_DIR": "",
                    "GC_WORK_DIR": str(per_bead_worktree),
                },
                script_root=rig_root,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(str(artifact), result.stdout)

    @staticmethod
    def _valid_review_artifact() -> str:
        return (
            "---\n"
            "schema: gc.build.review.v1\n"
            "workflow:\n"
            "  id: build-20260929-001\n"
            "  formula: build-from-plan\n"
            "methodology:\n"
            "  pack: gascity\n"
            "  name: build-from-plan\n"
            "producer:\n"
            "  formula: code-review-base\n"
            "  stage: review\n"
            "  attempt: 1\n"
            "status: approved\n"
            "trace:\n"
            "  upstream:\n"
            "    - path: implementation-plan.md\n"
            "      hash: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            "  coverage:\n"
            "    - id: GC-METH-001\n"
            "      status: covered\n"
            "---\n"
            "\n"
            "## Verdict\n\nApproved.\n\n"
            "| ID | Status |\n"
            "| --- | --- |\n"
            "| GC-METH-001 | covered |\n\n"
            "## Findings\n\nNone.\n\n"
            "## Verification\n\nRan the suite.\n"
        )

    def _run_materialized_review_check(
        self,
        tmp: pathlib.Path,
        *,
        installed_pack: pathlib.Path | None,
    ) -> subprocess.CompletedProcess:
        # Mirrors a city/rig root whose .gc/scripts holds copies of the pack's
        # check + validator: gc copies nothing else there, so no schemas/ sits
        # beside the copies (gc-qcs76). The pinned pack is only discoverable
        # through gc's formula search paths.
        rig_root = tmp / "root"
        artifact = rig_root / "plans" / "review-report.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(self._valid_review_artifact(), encoding="utf-8")
        per_bead_worktree = tmp / "per-bead-worktree"
        per_bead_worktree.mkdir()
        control = (
            '[{"id": "loop", "metadata": {'
            '"gc.root_bead_id": "root", '
            '"gc.build.artifact_schema": "gc.build.review.v1", '
            '"gc.build.artifact_path_keys": "gc.build.review_report_path"}}]'
        )
        root_bead = (
            '[{"id": "root", "metadata": {'
            '"gc.build.review_report_path": "plans/review-report.md"'
            '}}]'
        )
        extra_env = {
            "GC_RIG_ROOT": str(rig_root),
            "GC_WORK_DIR": str(per_bead_worktree),
            "GC_BUILD_SCHEMA_ROOTS": "",
        }
        if installed_pack is not None:
            formula_list = tmp / "formula-list.json"
            formula_list.write_text(
                json.dumps({"ok": True, "search_paths": [str(installed_pack / "formulas")]}),
                encoding="utf-8",
            )
            extra_env["FORMULA_LIST_JSON"] = str(formula_list)
        else:
            extra_env["FORMULA_LIST_JSON"] = ""
        return self._run_build_artifact_check(
            {"loop": control, "root": root_bead},
            "loop",
            extra_env=extra_env,
            script_root=rig_root,
        )

    def test_materialized_build_artifact_check_resolves_installed_pack_schemas(self) -> None:
        source_root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            self.assertFalse((tmp / "root" / "schemas").exists())
            result = self._run_materialized_review_check(tmp, installed_pack=source_root)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("build artifact valid: schema=gc.build.review.v1", result.stdout)

    def test_materialized_build_artifact_check_prefers_installed_pack_over_stale_local_schemas(self) -> None:
        # A hand-copied <root>/schemas/build (what ascent carries) is not kept
        # current across repins; the pinned pack's schemas must win over it.
        source_root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            stale = tmp / "root" / "schemas" / "build"
            stale.mkdir(parents=True)
            (stale / "review.v1.yaml").write_text(
                "schema_id: gc.build.review.v1\n"
                "required_front_matter: [schema, status, trace]\n"
                "allowed_statuses: [approved]\n"
                "coverage_statuses: [covered]\n"
                "required_sections: [Stale Section Removed Upstream]\n",
                encoding="utf-8",
            )
            result = self._run_materialized_review_check(tmp, installed_pack=source_root)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("build artifact valid: schema=gc.build.review.v1", result.stdout)

    def test_materialized_build_artifact_check_fails_closed_without_installed_pack(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = self._run_materialized_review_check(pathlib.Path(td), installed_pack=None)

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unknown build artifact schema 'gc.build.review.v1'", result.stderr)

    def test_build_artifact_check_blocks_invalid_artifact_with_repair_context(self) -> None:
        with tempfile.TemporaryDirectory() as artifact_dir:
            artifact = pathlib.Path(artifact_dir) / "requirements.md"
            artifact.write_text(
                self._valid_requirements_artifact().replace("status: approved", "status: bogus"),
                encoding="utf-8",
            )

            control = (
                '[{"id": "loop", "metadata": {'
                '"gc.root_bead_id": "root", '
                '"gc.build.artifact_schema": "gc.build.requirements.v1", '
                '"gc.build.artifact_path_keys": "gc.build.requirements_path,gc.var.requirements_path"}}]'
            )
            root_bead = (
                '[{"id": "root", "metadata": {'
                f'"gc.build.requirements_path": "{artifact}"'
                "}}]"
            )
            result = self._run_build_artifact_check(
                {"loop": control, "root": root_bead}, "loop"
            )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("failed validation", result.stderr)
        self.assertIn("error:", result.stderr)
        self.assertIn("status", result.stderr)

    def test_build_artifact_check_fails_when_no_artifact_path_recorded(self) -> None:
        control = (
            '[{"id": "loop", "metadata": {'
            '"gc.root_bead_id": "root", '
            '"gc.build.artifact_schema": "gc.build.requirements.v1", '
            '"gc.build.artifact_path_keys": "gc.build.requirements_path,gc.var.requirements_path"}}]'
        )
        root_bead = '[{"id": "root", "metadata": {}}]'
        result = self._run_build_artifact_check({"loop": control, "root": root_bead}, "loop")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no artifact path recorded", result.stderr)
        self.assertIn("gc.build.requirements_path,gc.var.requirements_path", result.stderr)

    def _run_coverage_permit_check(
        self,
        plan_text: str,
        *,
        step_permits: str | None = "required",
        root_metadata: dict[str, str] | None = None,
        requirements_text: str | None = COVERAGE_PERMIT_REQUIREMENTS,
        extra_beads: dict[str, str] | None = None,
        requirements_relative_to: str = "",
        parent_root_metadata: dict[str, str] | None = None,
        bead_errors: dict[str, str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        """root_metadata replaces the default requirements key on the step's
        workflow root. parent_root_metadata adds the workflow that launched it
        (root -> drain control bead `drain` -> `parent`); the placeholder
        `<requirements>` in its values is the requirements file's path."""
        with tempfile.TemporaryDirectory() as artifact_dir:
            root_dir = pathlib.Path(artifact_dir)
            plan = root_dir / "implementation-plan.md"
            plan.write_text(plan_text, encoding="utf-8")
            requirements = root_dir / "requirements.md"
            if requirements_text is not None:
                requirements.write_text(requirements_text, encoding="utf-8")
            step_meta = {
                "gc.root_bead_id": "root",
                "gc.build.artifact_schema": "gc.build.plan.v1",
                "gc.build.artifact_path_keys": "gc.build.plan_path,gc.var.plan_path",
            }
            if step_permits is not None:
                step_meta["gc.build.coverage_permits"] = step_permits
            root_meta = {"gc.build.plan_path": str(plan)}
            if root_metadata is None:
                root_meta["gc.var.requirements_path"] = (
                    "requirements.md" if requirements_relative_to else str(requirements)
                )
            else:
                root_meta.update(
                    {key: value.replace("<requirements>", str(requirements)) for key, value in root_metadata.items()}
                )
            beads = {
                "loop": json.dumps([{"id": "loop", "metadata": step_meta}]),
                **(extra_beads or {}),
            }
            if parent_root_metadata is not None:
                root_meta["gc.drain_control_id"] = "drain"
                parent_meta = {
                    key: value.replace("<requirements>", str(requirements)) for key, value in parent_root_metadata.items()
                }
                beads.setdefault("drain", json.dumps([{"id": "drain", "metadata": {"gc.root_bead_id": "parent"}}]))
                beads.setdefault("parent", json.dumps([{"id": "parent", "metadata": parent_meta}]))
            beads.setdefault("root", json.dumps([{"id": "root", "metadata": root_meta}]))
            env = {"GC_RIG_ROOT": str(root_dir)} if requirements_relative_to else {}
            return self._run_build_artifact_check(
                beads, "loop", extra_env={**env, **(extra_env or {})}, bead_errors=bead_errors
            )

    def test_build_artifact_check_rejects_unpermitted_deferral_when_step_requires_permits(self) -> None:
        result = self._run_coverage_permit_check(coverage_permit_plan(None))

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("failed validation", result.stderr)
        self.assertIn("trace.coverage[AC-2] (status 'deferred'): missing permit", result.stderr)

    def test_build_artifact_check_accepts_permit_quoted_from_root_requirements(self) -> None:
        for relative in ("", "rig-relative"):
            with self.subTest(requirements_path=relative or "absolute"):
                result = self._run_coverage_permit_check(
                    coverage_permit_plan(COVERAGE_PERMIT_QUOTE), requirements_relative_to=relative
                )

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("build artifact valid", result.stdout)
                self.assertIn("requirements=", result.stdout)
                self.assertIn("/requirements.md", result.stdout)

    def test_build_artifact_check_rejects_permit_that_is_not_in_the_requirements(self) -> None:
        result = self._run_coverage_permit_check(
            coverage_permit_plan("This criterion is left for the publish stage to observe.")
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("trace.coverage[AC-2]", result.stderr)
        self.assertIn("permit text is not in the requirements artifact", result.stderr)

    def test_build_artifact_check_leaves_permits_alone_unless_the_step_opts_in(self) -> None:
        # A step instantiated from an older formula carries no switch: its
        # artifact is validated exactly as before.
        result = self._run_coverage_permit_check(coverage_permit_plan(None), step_permits=None)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("requirements=", result.stdout)

    def test_build_artifact_check_fails_when_recorded_requirements_file_is_missing(self) -> None:
        result = self._run_coverage_permit_check(
            coverage_permit_plan(COVERAGE_PERMIT_QUOTE), requirements_text=None
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("recorded at gc.var.requirements_path", result.stderr)
        self.assertIn("coverage permits cannot be checked", result.stderr)

    def test_build_artifact_check_finds_the_requirements_on_the_workflow_that_launched_the_item(self) -> None:
        # build-basic and build-from-requirements write the requirements in
        # their own requirements stage and record the path on THEIR root. The
        # implementation item's root has no requirements key, so the gate used
        # to compare the quote with the work item's text and reject a correct
        # quote of requirements.md.
        work_item = json.dumps([{"id": "item-1", "title": "Guard team-less items", "description": "Do the work."}])
        for key in ("gc.build.requirements_path", "gc.var.requirements_path"):
            with self.subTest(parent_key=key):
                result = self._run_coverage_permit_check(
                    coverage_permit_plan(COVERAGE_PERMIT_QUOTE),
                    root_metadata={"gc.drain_member_id": "item-1"},
                    parent_root_metadata={key: "<requirements>"},
                    extra_beads={"item-1": work_item},
                )

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("/requirements.md", result.stdout)

        # The same walk rejects a quote that is not in the parent's requirements.
        rejected = self._run_coverage_permit_check(
            coverage_permit_plan("This criterion is out of scope for the item, says nobody."),
            root_metadata={"gc.drain_member_id": "item-1"},
            parent_root_metadata={"gc.build.requirements_path": "<requirements>"},
            extra_beads={"item-1": work_item},
        )
        self.assertNotEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
        self.assertIn("permit text is not in the requirements artifact", rejected.stderr)

    def test_build_artifact_check_never_checks_a_quote_against_the_work_item(self) -> None:
        # No root records a requirements artifact. The work item happens to
        # contain the quoted sentence; that must not make the permit valid.
        work_item = json.dumps(
            [
                {
                    "id": "item-1",
                    "title": "Guard team-less items",
                    "description": "AC-2: after merge the alerts auto-close. " + COVERAGE_PERMIT_QUOTE,
                }
            ]
        )
        for name, parent in (("no parent workflow", None), ("parent records nothing", {"gc.var.push": "true"})):
            with self.subTest(name):
                result = self._run_coverage_permit_check(
                    coverage_permit_plan(COVERAGE_PERMIT_QUOTE),
                    root_metadata={"gc.drain_member_id": "item-1"},
                    parent_root_metadata=parent,
                    extra_beads={"item-1": work_item},
                )

                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("the requirements path could not be resolved", result.stderr)
                self.assertIn("1 coverage entries are not 'covered' (AC-2)", result.stderr)
                self.assertIn("records gc.build.requirements_path or gc.var.requirements_path", result.stderr)
                top = "parent" if parent is not None else "root"
                self.assertIn(
                    f"gc bd update {top} --set-metadata gc.build.requirements_path=<absolute path to the requirements file>",
                    result.stderr,
                )
                self.assertNotIn("work-item", result.stdout + result.stderr)

    def test_build_artifact_check_without_requirements_passes_an_artifact_that_leaves_nothing_open(self) -> None:
        # A bare implementation convoy has no requirements artifact. With every
        # entry covered there is no quote to check and no id list to compare.
        covered = (
            coverage_permit_plan(None)
            .replace("status: deferred\n      rationale: Observable only after merge.\n", "status: covered\n")
            .replace("| AC-2 | deferred |", "| AC-2 | covered |")
        )
        result = self._run_coverage_permit_check(covered, root_metadata={})

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("requirements=unresolved", result.stdout)

    def test_build_artifact_check_requires_every_id_the_requirements_define(self) -> None:
        # The plan lists and covers AC-1 and AC-2 only; the requirements also
        # define AC-3. The artifact agrees with itself and still fails.
        requirements = COVERAGE_PERMIT_REQUIREMENTS + "- **AC-3 (post-merge, mayor):** the mayor records the deploy.\n"
        result = self._run_coverage_permit_check(
            coverage_permit_plan(COVERAGE_PERMIT_QUOTE), requirements_text=requirements
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("every requirement id the requirements artifact defines", result.stderr)
        self.assertIn("missing from", result.stderr)
        self.assertIn("requirements.md: AC-3", result.stderr)

        # A drain of one work item is the whole build: same rule.
        single = self._run_coverage_permit_check(
            coverage_permit_plan(COVERAGE_PERMIT_QUOTE),
            requirements_text=requirements,
            root_metadata={"gc.drain_count": "1", "gc.var.requirements_path": "<requirements>"},
        )
        self.assertNotEqual(single.returncode, 0, single.stdout + single.stderr)
        self.assertIn("requirements.md: AC-3", single.stderr)

    def test_build_artifact_check_does_not_ask_one_work_item_of_several_for_every_id(self) -> None:
        # The engine stamps the number of drain members on each item root. One
        # item of three delivers part of the requirements; its permits are
        # still checked.
        requirements = COVERAGE_PERMIT_REQUIREMENTS + "- **AC-3:** the other work item's criterion.\n"
        root_metadata = {"gc.drain_count": "3", "gc.drain_index": "1", "gc.var.requirements_path": "<requirements>"}
        accepted = self._run_coverage_permit_check(
            coverage_permit_plan(COVERAGE_PERMIT_QUOTE), requirements_text=requirements, root_metadata=root_metadata
        )
        unpermitted = self._run_coverage_permit_check(
            coverage_permit_plan(None), requirements_text=requirements, root_metadata=root_metadata
        )

        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertIn("requirement-ids=not-required(one-work-item-of-3)", accepted.stdout)
        self.assertNotEqual(unpermitted.returncode, 0, unpermitted.stdout + unpermitted.stderr)
        self.assertIn("trace.coverage[AC-2] (status 'deferred'): missing permit", unpermitted.stderr)
        self.assertNotIn("every requirement id", unpermitted.stderr)

    def test_build_artifact_check_rejects_a_permit_that_quotes_the_requirement_itself(self) -> None:
        result = self._run_coverage_permit_check(coverage_permit_plan("after merge the alerts auto-close."[6:]))
        # "merge the alerts auto-close." is in the requirements and hands nothing off.
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("does not hand the requirement off", result.stderr)

    def test_build_artifact_check_retries_a_bead_read_and_reports_no_verdict_not_a_failed_artifact(self) -> None:
        recovered = self._run_coverage_permit_check(
            coverage_permit_plan(COVERAGE_PERMIT_QUOTE),
            bead_errors={"root.error": "dolt circuit breaker is open\n", "root.flaky": "2"},
        )
        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        self.assertIn("build-artifact-check: INFRA gc bd show root failed: dolt circuit breaker is open", recovered.stderr)
        self.assertIn("without counting a failed attempt", recovered.stderr)

        down = self._run_coverage_permit_check(
            coverage_permit_plan(COVERAGE_PERMIT_QUOTE),
            bead_errors={"loop.error": "events: lock timed out\n", "loop.flaky": "100000"},
            extra_env={"BUILD_ARTIFACT_INFRA_BUDGET_SECONDS": "1"},
        )
        self.assertEqual(down.returncode, 75, down.stdout + down.stderr)
        self.assertIn("INFRA no verdict: gc bd show loop kept failing: events: lock timed out", down.stderr)
        self.assertNotIn("failed validation", down.stderr)

        # The parent workflow's beads are read through the same retry.
        parent_down = self._run_coverage_permit_check(
            coverage_permit_plan(COVERAGE_PERMIT_QUOTE),
            root_metadata={},
            parent_root_metadata={"gc.build.requirements_path": "<requirements>"},
            bead_errors={"drain.error": "i/o timeout\n", "drain.flaky": "100000"},
            extra_env={"BUILD_ARTIFACT_INFRA_BUDGET_SECONDS": "1"},
        )
        self.assertEqual(parent_down.returncode, 75, parent_down.stdout + parent_down.stderr)

        missing = self._run_build_artifact_check({}, "gone")
        self.assertEqual(missing.returncode, 1, missing.stdout + missing.stderr)
        self.assertIn("bead gone does not exist", missing.stderr)

    def test_coverage_permit_stages_carry_the_switch_and_explain_the_rule(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]

        for (formula_name, step_id), doc in COVERAGE_PERMIT_STEPS.items():
            with self.subTest(formula=formula_name, step=step_id):
                formula = load_formula(root, formula_name)
                step = {node["id"]: node for node in formula["steps"]}[step_id]
                self.assertEqual(step["metadata"].get("gc.build.coverage_permits"), "required")
                # The switch is read by the artifact gate, directly or through
                # a script that chains it.
                self.assertIn(
                    pathlib.PurePosixPath(step["check"]["check"]["path"]).name,
                    {"build-artifact-valid.sh", "implementation-handoff-valid.sh"},
                )
                text = " ".join((root / "assets" / "workflows" / doc).read_text(encoding="utf-8").split())
                for fragment in (
                    "`permit`",
                    "word for word",
                    "gc-gdyaz",
                    "`status: blocked`",
                    # Ids come from the requirements file, and a permit must
                    # hand the requirement off.
                    "`AC-1`, `SCOPE-2`, `REQ-3`",
                    "hands the requirement off",
                    '"out of scope"',
                    "own statement",
                    "is not a permit",
                ):
                    self.assertIn(fragment, text, f"{doc} must explain coverage permits ({fragment})")
                self.assertNotIn("the text of the work item", text)

        # No other producer stage enforces permits without telling its worker.
        for path in sorted((root / "formulas").glob("*.formula.toml")):
            formula = tomllib.loads(path.read_text(encoding="utf-8"))
            for step in formula.get("steps", []):
                if (step.get("metadata") or {}).get("gc.build.coverage_permits"):
                    self.assertIn((formula["formula"], step["id"]), COVERAGE_PERMIT_STEPS)

    def test_plan_stage_tells_the_author_how_to_account_for_each_requirement(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        plan = (root / "assets" / "workflows" / "build-from-plan-base" / "plan.md").read_text(encoding="utf-8")
        skill = (root / "skills" / "mayor" / "SKILL.md").read_text(encoding="utf-8")

        for fragment in (
            "Account for every requirement.",
            "`trace.upstream[].ids`",
            "`{{requirements_path}}`",
            "A requirement already satisfied on the base branch",
            "A plan that is not\n  approved needs no permits.",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, plan)
        self.assertIn("State hand-offs in the requirements themselves.", skill)
        self.assertIn("`permit`", skill)
        self.assertIn("in words the build gate recognises", skill)
        self.assertIn("the gate takes the ids from\nthe requirements file", skill)
        # Every hand-off phrase the docs teach is one the validator accepts.
        spec = importlib.util.spec_from_file_location(
            "validate_build_artifact_for_docs", root / "assets" / "scripts" / "validate_build_artifact.py"
        )
        validator = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = validator  # dataclasses resolve the module by name
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(validator)
        taught = {
            "plan.md": (
                " ".join(plan.split()),
                ("post-merge", "after merge", "next round", "follow-up bead", "out of scope", "not in this work",
                 "do not touch", "mayor-owned", "the mayor checks", "Jon's decision", "deferred", "blocked on"),
            ),
            "SKILL.md": (
                " ".join(skill.split()),
                ("post-merge", "after merge", "next round", "out of scope", "not in this work", "follow-up bead",
                 "separate bead", "mayor-owned", "the mayor checks", "Jon's decision", "deferred"),
            ),
        }
        for doc, (text, phrases) in taught.items():
            for phrase in phrases:
                with self.subTest(doc=doc, phrase=phrase):
                    self.assertIn(f'"{phrase}"', text)
                    self.assertTrue(validator.permit_cue(validator.normalize_permit_text(phrase)), phrase)
        for phrase in validator.PERMIT_CUE_EXAMPLES.replace('"', "").split(", "):
            with self.subTest(example=phrase):
                self.assertTrue(validator.permit_cue(validator.normalize_permit_text(phrase)), phrase)

    def test_review_report_prompt_writes_to_the_rig_root(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        prompt = (root / "assets" / "workflows" / "review" / "write-report.md").read_text(
            encoding="utf-8"
        )

        for fragment in (
            "GC_RIG_ROOT",
            "per-bead worktree",
            "gc.build.review_report_path={{report_path}}",
            "or `$GC_WORK_DIR`",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, prompt)

    def test_bmad_story_development_emits_base_check_verdict(self) -> None:
        gascity_root = pathlib.Path(__file__).resolve().parents[1]
        bmad_root = gascity_root.parent / "bmad"

        for formula_name, step_id in (
            ("bmad-story-development", "implement"),
            ("bmad-story-development-item", "implement-item"),
        ):
            with self.subTest(formula=formula_name):
                formula = load_formula(bmad_root, formula_name)
                step = next(step for step in formula["steps"] if step["id"] == step_id)
                self.assertEqual(
                    step["check"]["check"]["path"],
                    "../assets/scripts/checks/implementation-review-approved.sh",
                )

        story_root = bmad_root / "assets" / "workflows" / "bmad-story-development"
        setup_text = (story_root / "setup-bmad-story-development.md").read_text(encoding="utf-8")
        self.assertIn("gc.outcome=pass", setup_text)

        apply_text = (story_root / "apply-story-findings.md").read_text(encoding="utf-8")
        self.assertIn("bmad_story.verdict=done", apply_text)
        self.assertIn("bmad_story.verdict=iterate", apply_text)
        self.assertIn("bmad_story.report_path=<fix summary path>", apply_text)
        self.assertIn("code_review.verdict=done", apply_text)
        self.assertIn("code_review.verdict=iterate", apply_text)
        self.assertIn("code_review.report_path=<fix summary path>", apply_text)

    def test_implementation_review_check_takes_newest_verdict_not_highest_id(self) -> None:
        """`| last` in the verdict extractors has to mean newest, not highest id.

        gmol dedupes the four status legs with `unique_by(.id)`, and jq's
        unique_by sorts — so without a re-sort the union arrives in bead-id
        order and this gate's `| last` picks a verdict by id hash. Here the
        stale `iterate` sorts after the newer `done`, so an already-approved
        review would loop until Ralph ran out of attempts: the exact symptom
        the federating-reader fix was written to end, re-entering by ordering
        rather than by starvation.
        """
        show_json = json.dumps(
            [{"id": "loop", "metadata": {"gc.root_bead_id": "root", "gc.attempt": "1"}}]
        )

        def member(bead_id: str, updated: str, verdict: str) -> dict:
            return {
                "id": bead_id,
                "updated_at": updated,
                "metadata": {
                    "gc.root_bead_id": "root",
                    "gc.attempt": "1",
                    "code_review.verdict": verdict,
                    "code_review.report_path": f"/reports/{bead_id}.md",
                },
            }

        # "gcg-zzz" sorts last by id but carries the OLDER verdict.
        list_json = json.dumps(
            [
                member("gcg-aaa", "2026-08-13T03:00:00Z", "done"),
                member("gcg-zzz", "2026-08-13T01:00:00Z", "iterate"),
            ]
        )
        result = self._run_implementation_review_check(show_json=show_json, list_json=list_json)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_design_review_check_unions_every_status_leg(self) -> None:
        """The verdict usually lands on a bead the review just closed.

        `gc ready` takes exactly one --status, so the gate queries open,
        in_progress, blocked and closed and unions the results. Drop any leg and
        a verdict parked in that state disappears — which is the original bug:
        the gate reports a member set it could not actually read, and Ralph
        iterates until it runs out of attempts. Pin the closed leg specifically,
        because that is where an approval comes to rest.
        """
        root = pathlib.Path(__file__).resolve().parents[1]
        script = root / "assets" / "scripts" / "checks" / "design-review-approved.sh"

        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            write_check_gc_stub(bin_dir)

            show_json = tmp / "show.json"
            list_json = tmp / "list.json"
            show_json.write_text(
                json.dumps(
                    [{"id": "loop", "metadata": {"gc.root_bead_id": "root", "gc.attempt": "1"}}]
                ),
                encoding="utf-8",
            )
            list_json.write_text(
                json.dumps(
                    [
                        {
                            "id": "approved-and-closed",
                            "status": "closed",
                            "metadata": {
                                "gc.root_bead_id": "root",
                                "gc.attempt": "1",
                                "gc.continuation_group": "design-review-fixes",
                                "design_review.verdict": "done",
                            },
                        }
                    ]
                ),
                encoding="utf-8",
            )

            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "BD_SHOW_JSON": str(show_json),
                "BD_LIST_JSON": str(list_json),
                "GC_BEAD_ID": "loop",
            }
            result = subprocess.run(
                [str(script)], env=env, text=True, capture_output=True, check=False
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Design review approved", result.stdout)

    def test_design_review_check_scopes_verdict_to_current_loop(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        script = root / "assets" / "scripts" / "checks" / "design-review-approved.sh"

        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            write_check_gc_stub(bin_dir)

            show_json = tmp / "show.json"
            list_json = tmp / "list.json"
            show_json.write_text(
                """[
  {
    "id": "loop",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.step_ref": "requirements.superpowers-brainstorming-loop.iteration.1"
    }
  }
]""",
                encoding="utf-8",
            )
            list_json.write_text(
                """[
  {
    "id": "current-loop-feedback",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.scope_ref": "requirements.superpowers-brainstorming-loop.iteration.1",
      "gc.continuation_group": "design-review-fixes",
      "design_review.verdict": "iterate"
    }
  },
  {
    "id": "old-loop-approval",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.scope_ref": "plan-review.superpowers-plan-review-loop.iteration.1",
      "gc.continuation_group": "design-review-fixes",
      "design_review.verdict": "done"
    }
  }
]""",
                encoding="utf-8",
            )

            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "BD_SHOW_JSON": str(show_json),
                "BD_LIST_JSON": str(list_json),
                "GC_BEAD_ID": "loop",
            }
            result = subprocess.run(
                [str(script)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("needs another pass", result.stdout)

    def test_design_review_check_finds_verdict_from_logical_loop_root(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        script = root / "assets" / "scripts" / "checks" / "design-review-approved.sh"

        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            write_check_gc_stub(bin_dir)

            show_json = tmp / "show.json"
            list_json = tmp / "list.json"
            show_json.write_text(
                """[
  {
    "id": "loop-root",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.step_id": "requirements.superpowers-design-approval-loop",
      "gc.step_ref": "superpowers-build.requirements.superpowers-design-approval-loop"
    }
  }
]""",
                encoding="utf-8",
            )
            list_json.write_text(
                """[
  {
    "id": "unrelated-plan-approval",
    "updated_at": "2026-06-08T09:40:00Z",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "plan-review.superpowers-plan-review-loop",
      "gc.scope_ref": "plan-review.superpowers-plan-review-loop.iteration.1",
      "design_review.verdict": "done"
    }
  },
  {
    "id": "design-review-feedback",
    "updated_at": "2026-06-08T09:41:00Z",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "requirements.superpowers-design-approval-loop",
      "gc.scope_ref": "requirements.superpowers-design-approval-loop.iteration.1",
      "design_review.verdict": "iterate"
    }
  },
  {
    "id": "design-approval",
    "updated_at": "2026-06-08T09:42:00Z",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "requirements.superpowers-design-approval-loop",
      "gc.scope_ref": "requirements.superpowers-design-approval-loop.iteration.1",
      "design_review.verdict": "done"
    }
  }
]""",
                encoding="utf-8",
            )

            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "BD_SHOW_JSON": str(show_json),
                "BD_LIST_JSON": str(list_json),
                "GC_BEAD_ID": "loop-root",
            }
            result = subprocess.run(
                [str(script)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Design review approved", result.stdout)

    def test_design_review_check_finds_verdict_from_child_loop_member(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        script = root / "assets" / "scripts" / "checks" / "design-review-approved.sh"

        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            write_check_gc_stub(bin_dir)

            show_json = tmp / "show.json"
            list_json = tmp / "list.json"
            show_json.write_text(
                """[
  {
    "id": "design-approval-child",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.step_id": "requirements.confirm-design-approval",
      "gc.step_ref": "requirements.superpowers-design-approval-loop.iteration.1.requirements.confirm-design-approval",
      "gc.scope_ref": "requirements.superpowers-design-approval-loop.iteration.1"
    }
  }
]""",
                encoding="utf-8",
            )
            list_json.write_text(
                """[
  {
    "id": "design-approval-child",
    "updated_at": "2026-06-08T09:42:00Z",
    "metadata": {
      "gc.root_bead_id": "root",
      "gc.attempt": "1",
      "gc.ralph_step_id": "requirements.superpowers-design-approval-loop",
      "gc.scope_ref": "requirements.superpowers-design-approval-loop.iteration.1",
      "design_review.verdict": "done"
    }
  }
]""",
                encoding="utf-8",
            )

            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "BD_SHOW_JSON": str(show_json),
                "BD_LIST_JSON": str(list_json),
                "GC_BEAD_ID": "design-approval-child",
            }
            result = subprocess.run(
                [str(script)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Design review approved", result.stdout)

    def test_superpowers_plan_review_loop_has_single_verdict_owner(self) -> None:
        packs_root = pathlib.Path(__file__).resolve().parents[2]
        setup = (
            packs_root
            / "superpowers"
            / "assets"
            / "workflows"
            / "superpowers-plan-review"
            / "{target}.setup-superpowers-plan-review.md"
        ).read_text(encoding="utf-8")
        review = (
            packs_root
            / "superpowers"
            / "assets"
            / "workflows"
            / "superpowers-plan-review"
            / "{target}.plan-document-review.md"
        ).read_text(encoding="utf-8")
        apply = (
            packs_root
            / "superpowers"
            / "assets"
            / "workflows"
            / "superpowers-plan-review"
            / "{target}.apply-plan-feedback.md"
        ).read_text(encoding="utf-8")
        finalize = (
            packs_root
            / "superpowers"
            / "assets"
            / "workflows"
            / "superpowers-plan-review"
            / "{target}.md"
        ).read_text(encoding="utf-8")

        self.assertNotIn("design_review.verdict=", review)
        self.assertIn("design_review.review_verdict", review)
        self.assertIn("design_review.verdict=done|iterate", apply)
        self.assertIn("gc.outcome=pass", apply)

        self.assertIn("plan-review-context.md", setup)
        self.assertIn("plan-review-report.md", setup)
        self.assertIn("plan-review-apply-summary.md", setup)
        self.assertIn("gc.build.plan_review_context_path", setup)
        self.assertIn("gc.build.plan_review_report_path", setup)
        self.assertIn("gc.build.plan_review_apply_summary_path", setup)
        self.assertIn("gc.build.plan_review_context_path", review)
        self.assertIn("gc.build.plan_review_report_path", review)
        self.assertIn("gc.build.plan_review_apply_summary_path", apply)
        self.assertIn("gc.build.plan_review_status=approved", finalize)
        self.assertIn("gc.build.plan_review_approved_at", finalize)
        self.assertIn("gc.build.plan_review_report_path", finalize)
        self.assertIn("gc.build.plan_review_apply_summary_path", finalize)
        self.assertIn("gc.build.plan_review_status=failed", finalize)

    def test_superpowers_code_review_loop_has_single_verdict_owner(self) -> None:
        packs_root = pathlib.Path(__file__).resolve().parents[2]
        workflow_dir = (
            packs_root
            / "superpowers"
            / "assets"
            / "workflows"
            / "superpowers-code-review"
        )
        expansion_formula = tomllib.loads(
            (
                packs_root
                / "superpowers"
                / "formulas"
                / "superpowers-code-review.formula.toml"
            ).read_text(encoding="utf-8")
        )
        review_formula = tomllib.loads(
            (
                packs_root
                / "superpowers"
                / "formulas"
                / "superpowers-review.formula.toml"
            ).read_text(encoding="utf-8")
        )
        setup = (workflow_dir / "{target}.setup-superpowers-code-review.md").read_text(
            encoding="utf-8"
        )
        request = (workflow_dir / "{target}.request-code-review.md").read_text(
            encoding="utf-8"
        )
        gap = (workflow_dir / "{target}.gap-analysis-review.md").read_text(
            encoding="utf-8"
        )
        process = (workflow_dir / "{target}.process-code-review.md").read_text(
            encoding="utf-8"
        )
        finalize = (workflow_dir / "{target}.md").read_text(encoding="utf-8")

        self.assertIn("code-review-context.md", setup)
        self.assertIn("implementation-review-report.md", setup)
        self.assertIn("gap-analysis-report.md", setup)
        self.assertIn("review-fix-summary.md", setup)
        self.assertIn("gc.build.code_review_context_path", setup)
        self.assertIn("gc.build.code_review_report_path", setup)
        self.assertIn("gc.build.gap_analysis_report_path", setup)
        self.assertIn("gc.build.review_fix_summary_path", setup)
        self.assertIn("implementation_convoy_id: not provided", setup)
        self.assertIn("Do not invent an external convoy", setup)
        self.assertIn("per-bead worktree", setup)

        self.assertIn("code_review.review_verdict", request)
        self.assertIn("code_review.review_report_path", request)
        self.assertIn("valid for `gc.build.review.v1`", request)
        self.assertIn("schema: gc.build.review.v1", request)
        self.assertIn("producer:", request)
        self.assertIn("stage: request-code-review", request)
        self.assertIn("| ID | Status |", request)
        self.assertIn("Use only schema\nallowed coverage statuses", request)
        self.assertIn("For `status: changes_required`, use\n`blocked`", request)
        self.assertIn("include\n`rationale: <why this id is blocked>`", request)
        self.assertIn("not use `violated`, `resolved`, `approved`, or `changes_required`", request)
        self.assertNotIn("code_review.verdict=done", request)
        self.assertNotIn("code_review.report_path=<", request)

        self.assertIn("code_review.gap_verdict", gap)
        self.assertIn("code_review.gap_report_path", gap)
        self.assertIn("valid for `gc.build.review.v1`", gap)
        self.assertIn("schema: gc.build.review.v1", gap)
        self.assertIn("stage: gap-analysis-review", gap)
        self.assertIn("| ID | Status |", gap)
        self.assertIn("include\n`rationale: <why this id is blocked>`", gap)
        self.assertIn("not use `violated`, `resolved`, `approved`, or `changes_required`", gap)
        self.assertNotIn("code_review.verdict=done", gap)
        self.assertNotIn("code_review.report_path=<", gap)

        for reviewer_name, reviewer in (("request", request), ("gap", gap)):
            with self.subTest(reviewer=reviewer_name):
                self.assertIn("Initial-review baseline", reviewer)
                self.assertIn("highest numeric `gc.attempt`", reviewer)
                self.assertIn("gc.build.review_fix_summary_path", reviewer)
                self.assertIn("fixed implementation worktree", reviewer)
                self.assertIn("must not issue an `iterate` verdict merely", reviewer)

        self.assertIn("may be intentionally absent", gap)
        self.assertIn("must not by itself cause `iterate`", gap)
        self.assertIn("implementation_convoy_id: not provided", gap)

        self.assertIn("code_review.verdict=done|iterate", process)
        self.assertIn("code_review.report_path=<review fix summary path>", process)
        self.assertIn("Use `covered` for resolved\nfindings", process)
        self.assertIn("Include `rationale: <why this id is not covered>`", process)
        self.assertIn("gc.build.code_review_status=approved", process)
        self.assertIn("gc.build.code_review_status=draft", process)
        self.assertIn("non-blocking for this standalone review", process)
        self.assertIn("implementation_convoy_id: not provided", process)
        self.assertIn("must not self-approve its own remediation", process)
        self.assertIn("next attempt must independently re-review", process)

        self.assertIn("gc.build.code_review_status=approved", finalize)
        self.assertIn("gc.build.code_review_approved_at", finalize)
        self.assertIn("gc.build.code_review_status=failed", finalize)

        artifact_keys = (
            "gc.build.code_review_report_path,"
            "gc.build.review_report_path,"
            "gc.var.report_path"
        )
        self.assertEqual(
            expansion_formula["vars"]["artifact_path_keys"]["default"],
            artifact_keys,
        )
        write_report_step = next(
            step
            for step in review_formula["steps"]
            if step["id"] == "write-report"
        )
        self.assertEqual(
            write_report_step["metadata"]["gc.build.artifact_path_keys"],
            artifact_keys,
        )
        self.assertEqual(
            write_report_step["expand_vars"]["artifact_path_keys"],
            artifact_keys,
        )



REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACK_CHECK_ASSET_PREFIX = "../assets/scripts/checks/"
MATERIALIZED_CHECK_PREFIX = ".gc/scripts/checks/"


def _repo_packs() -> dict[str, pathlib.Path]:
    return {
        pack_toml.parent.name: pack_toml.parent
        for pack_toml in sorted(REPO_ROOT.glob("*/pack.toml"))
    }


def _pack_layer_roots(pack_dir: pathlib.Path) -> list[pathlib.Path]:
    """The pack's own dir followed by every pack it imports (local sources)."""
    roots = [pack_dir]
    data = tomllib.loads((pack_dir / "pack.toml").read_text(encoding="utf-8"))
    for spec in (data.get("imports") or {}).values():
        source = spec.get("source", "") if isinstance(spec, dict) else ""
        if source.startswith("."):
            imported = (pack_dir / source).resolve()
            if (imported / "pack.toml").is_file():
                roots.append(imported)
    return roots


def _formula_check_paths(steps: list[dict], out: list[tuple[str, str]]) -> None:
    for step in steps or []:
        check = (step.get("check") or {}).get("check") or {}
        if check.get("path"):
            out.append((step.get("id", "?"), check["path"]))
        _formula_check_paths(step.get("children") or [], out)
        _formula_check_paths((step.get("loop") or {}).get("body") or [], out)


class PackCheckPathContractTests(unittest.TestCase):
    """gc-yrouy: formula gates must run the pinned pack's check scripts.

    `.gc/scripts/checks/<name>.sh` is resolved by the controller against the
    rig/city root, where only hand-copied snapshots live (gc never
    materializes pack assets there), so every pack change to a check script
    was silently ignored. gc's formula parser rewrites only the
    `../assets/scripts/checks/<name>.sh` form to the highest-priority formula
    layer that ships the script, which follows every repin.
    """

    def test_no_pack_formula_or_prompt_references_materialized_check_scripts(self) -> None:
        offenders = []
        for pack_name, pack_dir in _repo_packs().items():
            for pattern in ("formulas/**/*.toml", "assets/**/*.md", "assets/**/*.toml"):
                for path in sorted(pack_dir.glob(pattern)):
                    for lineno, line in enumerate(
                        path.read_text(encoding="utf-8").splitlines(), start=1
                    ):
                        if MATERIALIZED_CHECK_PREFIX in line or ("cp -R" in line and ".gc/scripts" in line):
                            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
        self.assertEqual(
            offenders,
            [],
            "formula gates and prompts must use the layer-resolved "
            f"{PACK_CHECK_ASSET_PREFIX}<name>.sh form, not {MATERIALIZED_CHECK_PREFIX}",
        )

    def test_every_formula_check_path_resolves_to_a_shipped_executable_script(self) -> None:
        checked = 0
        for pack_name, pack_dir in _repo_packs().items():
            layers = _pack_layer_roots(pack_dir)
            for formula_path in sorted((pack_dir / "formulas").glob("*.toml")):
                formula = tomllib.loads(formula_path.read_text(encoding="utf-8"))
                paths: list[tuple[str, str]] = []
                _formula_check_paths(formula.get("steps") or [], paths)
                _formula_check_paths(formula.get("template") or [], paths)
                for step_id, raw in paths:
                    checked += 1
                    with self.subTest(pack=pack_name, formula=formula_path.name, step=step_id):
                        self.assertTrue(
                            raw.startswith(PACK_CHECK_ASSET_PREFIX),
                            f"check path {raw!r} is not layer-resolved",
                        )
                        rel = raw[len("../assets/"):]
                        shipped = [
                            layer / "assets" / rel
                            for layer in layers
                            if (layer / "assets" / rel).is_file()
                        ]
                        self.assertTrue(shipped, f"no layer ships {raw!r} (layers: {layers})")
                        self.assertTrue(
                            os.access(shipped[0], os.X_OK), f"{shipped[0]} is not executable"
                        )
        self.assertGreater(checked, 70)

    def test_build_artifact_check_from_pack_layer_resolves_relative_path_under_ralph_env(self) -> None:
        # Reproduces internal/dispatch/ralph.go runRalphCheck: the parser has
        # rewritten the check path to <pack-cache>/gascity/assets/scripts/checks,
        # cmd.Dir is the store root, and the env is the ConditionEnv whitelist
        # (GC_STORE_PATH / GC_CITY*; no GC_RIG_ROOT, GC_BEADS_SCOPE_ROOT or
        # GC_DIR; GC_WORK_DIR only when the bead inherits a work_dir).
        for label, with_work_dir in (("no-work-dir", False), ("per-bead-work-dir", True)):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as td:
                result, artifact = self._run_pack_layer_build_artifact_check(
                    pathlib.Path(td), with_work_dir=with_work_dir
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"path={artifact}", result.stdout)

    def test_build_artifact_check_from_pack_layer_uses_pack_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result, _ = self._run_pack_layer_build_artifact_check(
                pathlib.Path(td), with_work_dir=False, schema="gc.build.review.v1"
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("schema=gc.build.review.v1", result.stdout)

    def test_manual_check_command_resolves_last_layer_that_ships_the_script(self) -> None:
        # Mirrors formula.Parser.winningAssetPath: search paths are ordered
        # lowest -> highest priority and the last layer that ships the asset wins.
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            layers = []
            for name, ships in (("low", True), ("gascity", True), ("city-local", False)):
                formulas = tmp / name / "formulas"
                formulas.mkdir(parents=True)
                if ships:
                    script = tmp / name / "assets" / "scripts" / "checks" / "build-artifact-valid.sh"
                    script.parent.mkdir(parents=True)
                    script.write_text(f"#!/bin/sh\necho ran-{name} bead=$GC_BEAD_ID\n", encoding="utf-8")
                    script.chmod(0o755)
                layers.append(str(formulas))
            listing = tmp / "formula-list.json"
            listing.write_text(json.dumps({"search_paths": layers}), encoding="utf-8")
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            fake_gc = bin_dir / "gc"
            fake_gc.write_text(f"#!/bin/sh\ncat '{listing}'\n", encoding="utf-8")
            fake_gc.chmod(0o755)
            command = MANUAL_BUILD_ARTIFACT_CHECK_COMMAND.replace("<claimed-step-id>", "step-1")
            env = {
                **os.environ,
                "PATH": os.pathsep.join([str(bin_dir), str(pathlib.Path(sys.executable).parent), "/usr/bin", "/bin"]),
            }
            result = subprocess.run(
                ["bash", "-c", command], env=env, text=True, capture_output=True, check=False
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "ran-gascity bead=step-1")

    def test_manual_check_prompts_use_the_layer_resolving_command(self) -> None:
        root = REPO_ROOT / "gascity" / "assets" / "workflows"
        for relative_path in (
            "do-work/implement.md",
            "do-work-item/implement-item.md",
            "implementation-base/implement.md",
            "implementation-item-base/implement-item.md",
            "implement/summarize.md",
            "build-base/summarize-implementation.md",
            "review/write-report.md",
        ):
            with self.subTest(asset=relative_path):
                self.assertIn(
                    MANUAL_BUILD_ARTIFACT_CHECK_COMMAND,
                    (root / relative_path).read_text(encoding="utf-8"),
                )

    def _run_pack_layer_build_artifact_check(
        self,
        tmp: pathlib.Path,
        *,
        with_work_dir: bool,
        schema: str = "gc.build.requirements.v1",
    ) -> tuple[subprocess.CompletedProcess, pathlib.Path]:
        source_pack = REPO_ROOT / "gascity"
        cached_pack = tmp / "gc-home" / "cache" / "repos" / "0123abcd" / "gascity"
        shutil.copytree(source_pack / "assets" / "scripts", cached_pack / "assets" / "scripts")
        shutil.copytree(source_pack / "schemas" / "build", cached_pack / "schemas" / "build")
        shutil.copy2(source_pack / "pack.toml", cached_pack / "pack.toml")
        check = cached_pack / "assets" / "scripts" / "checks" / "build-artifact-valid.sh"

        city = tmp / "city"
        store = tmp / "rig"
        (city / ".gc").mkdir(parents=True)
        (store / ".beads").mkdir(parents=True)
        if schema == "gc.build.review.v1":
            body, key = FormulaAssetTests._valid_review_artifact(), "gc.build.review_report_path"
        else:
            body, key = FormulaAssetTests._valid_requirements_artifact(), "gc.build.requirements_path"
        artifact = store / "plans" / "run-1" / "artifact.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(body, encoding="utf-8")

        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        show_dir = tmp / "show"
        show_dir.mkdir()
        (show_dir / "loop.json").write_text(
            json.dumps([{"id": "loop", "metadata": {
                "gc.root_bead_id": "root",
                "gc.build.artifact_schema": schema,
                "gc.build.artifact_path_keys": key,
            }}]),
            encoding="utf-8",
        )
        (show_dir / "root.json").write_text(
            json.dumps([{"id": "root", "metadata": {key: "plans/run-1/artifact.md"}}]),
            encoding="utf-8",
        )
        fake_gc = bin_dir / "gc"
        fake_gc.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if [ \"${1:-}\" = \"formula\" ]; then echo 'unexpected gc formula call' >&2; exit 3; fi\n"
            "while [ \"${1:-}\" != \"bd\" ]; do [ $# -gt 0 ] || exit 2; shift; done\n"
            "shift\n"
            "case \"$1\" in\n"
            "  show) cat \"" + str(show_dir) + "/$2.json\" ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake_gc.chmod(0o755)

        python_dir = pathlib.Path(sys.executable).parent
        env = {
            # convergence.ConditionEnv.Environ(): a whitelist, not os.environ.
            "PATH": os.pathsep.join([str(bin_dir), str(python_dir), "/usr/bin", "/bin"]),
            "HOME": str(city),
            "GC_HOME": str(tmp / "gc-home"),
            "TMPDIR": str(tmp),
            "BEADS_DIR": str(store / ".beads"),
            "GC_BEAD_ID": "loop",
            "GC_ITERATION": "1",
            "GC_WISP_ID": "",
            "GC_ITERATION_DURATION_MS": "0",
            "GC_CUMULATIVE_DURATION_MS": "0",
            "GC_MAX_ITERATIONS": "0",
            "GC_CITY": str(city),
            "GC_CITY_PATH": str(city),
            "GC_CITY_RUNTIME_DIR": str(city / ".gc" / "runtime"),
            "GC_STORE_PATH": str(store),
        }
        cwd = store
        if with_work_dir:
            work_dir = tmp / "worktrees" / "per-bead"
            work_dir.mkdir(parents=True)
            env["GC_WORK_DIR"] = str(work_dir)
            cwd = work_dir
        result = subprocess.run(
            [str(check)], cwd=cwd, env=env, text=True, capture_output=True, check=False
        )
        return result, artifact


if __name__ == "__main__":
    unittest.main()
