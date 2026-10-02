#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    tomllib = None


FRONT_MATTER_RE = re.compile(r"\A---\n(?P<front>.*?)\n---(?:\n|\Z)(?P<body>.*)\Z", re.DOTALL)
SCRIPT_DIR = Path(__file__).resolve().parent
# In the pack tree this script lives at <pack>/assets/scripts/, so the base
# schemas sit at <pack>/schemas/build.
SCHEMA_ROOT = SCRIPT_DIR.parents[1] / "schemas" / "build"
BASE_PACK_NAME = "gascity"
PACK_NAME_RE = re.compile(r'^\[pack\][^\[]*?^name\s*=\s*"([^"]*)"', re.MULTILINE | re.DOTALL)
INSTALLED_PACK_LOOKUP_TIMEOUT_SECONDS = 60
FORBIDDEN_REQUIRED_FIELD_NAMES = {"owner", "stage-owner", "stage_owner", "persona", "role"}

# Coverage permits (gc-gdyaz). A producer stage that opts in (step metadata
# gc.build.coverage_permits=required, passed here as --require-coverage-permits)
# is held to two rules, both checked against the requirements artifact the
# workflow records, never against what the artifact says about itself:
#
# 1. Every requirement id the requirements artifact defines has a coverage
#    entry. An artifact cannot drop a requirement by leaving it out of both
#    its own `trace.upstream[].ids` and its coverage.
# 2. A coverage entry at any status other than "covered" carries a `permit`: a
#    word-for-word quote of requirements text that hands the requirement off.
#    The quote must contain hand-off words (PERMIT_CUES). A requirement's own
#    statement is in the requirements too, so "the text is there" alone would
#    let any requirement permit its own deferral.
#
# An artifact whose own status already says the work is not approved is held
# to neither rule: it stops the build by itself. Rule 1 is not applied to the
# summary of one work item in a drain of several (--partial-coverage): it
# delivers part of the requirements, and they do not say which part.
PERMIT_EXEMPT_ARTIFACT_STATUSES = frozenset({"blocked", "questions", "changes_required", "superseded"})
MIN_PERMIT_CHARS = 20
PERMIT_FOLD_TABLE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\u2026": "...",
    }
)
PERMIT_LINK_RE = re.compile(r"!?\[([^\]\n]*)\]\([^)\n]*\)")
PERMIT_BLOCKQUOTE_RE = re.compile(r"(?m)^[ \t]*(?:>[ \t]?)+")
PERMIT_LINE_MARKER_RE = re.compile(r"(?m)^[ \t]*(?:#{1,6}[ \t]+|(?:[-*+]|\d{1,3}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?)")
PERMIT_MARKDOWN_NOISE_RE = re.compile(r"[`*_~\\]")
PERMIT_ELLIPSIS_RE = re.compile(r"\s*\.{3,}\s*")
PERMIT_LONE_DASH_RE = re.compile(r"(?<=\s)-+(?=\s)")
PERMIT_EDGE_ELLIPSIS_RE = re.compile(r"^(?:\.\.\.\s*)+|(?:\s*\.\.\.)+$")

# Hand-off words a permit must contain, matched on the folded (lower-case,
# Markdown-free) quote. Chosen from the requirements corpus in
# /Users/fernanja/gc/plans (111 files): each group is how those files say a
# requirement is someone else's, for later, or not for this work.
PERMIT_CUES: tuple[tuple[str, str], ...] = (
    (
        "a later moment",
        r"\bpost-?merge\b|\bpost-?deploy\b"
        r"|\bafter (?:the |this |that |it |a |its )?(?:pr |pull request |build |change |fix |work |workflow |branch )?"
        r"(?:is |has |gets |have |are |been )?(?:merge|merges|merged|merging|lands|landed|ships|shipped|deploy|deploys|deployed)\b"
        r"|\bonce (?:the |this |that |it )?(?:pr |pull request |build |change |fix )?(?:is |has |gets )?(?:merged|landed|shipped|deployed)\b"
        r"|\bnext round\b|\b(?:later|future|subsequent) (?:stage|step|part|round|bead|pr|pull request|build|workflow|change|task|issue)\b"
        r"|\bfollow[- ]?up beads?\b|\bas a follow[- ]?up\b|\b(?:mayor|mayor's|tracked) follow[- ]?ups?\b",
    ),
    (
        "an exclusion",
        r"\bout[- ]of[- ]scope\b|\bnot in scope\b|\boutside (?:the |this )?scope\b|\bnon-?goals?\b"
        r"|\bnot (?:a )?part of (?:this|the)\b"
        r"|\bnot in this (?:work|bead|build|pr|pull request|round|change|fix|part|workflow|task)\b"
        r"|\bnot (?:done|fixed|changed|built|required|needed|addressed|included) (?:here|in this)\b"
        r"|\bnot this (?:pr|bead|build)'s to (?:fix|do|decide)\b"
        r"|\bseparate (?:bead|pr|pull request|build|workflow|round|change|issue|task)\b|\bits own (?:bead|pr|pull request)\b"
        r"|\b(?:do not|don't|never) (?:touch|attempt|write (?:in)?to)\b|\bdo not execute\b"
        r"|\bsupersed(?:e|es|ed)\b|\b(?:replaces|amends) (?:[a-z]+-\d+|the (?:original|earlier|prior|previous))",
    ),
    (
        "another owner",
        r"\bmayor-(?:owned|side)\b|\bmayor \(not the worker\)|\bnot (?:a|the) worker\b"
        r"|\bthe mayor (?:then |also |will |alone |must )?(?:owns|checks|triggers|records|repins|publishes|republishes|handles|decides|verifies|runs|relays|merges|does|performs|answers|closes|rebases|reviews|writes|propagates|gates|files)\b"
        r"|\bmayor's (?:decision|call|job|check|follow[- ]?up)\b|\bfor the mayor to \w+"
        r"|\b(?:repaired|handled|assembled|done|run|checked|published|decided|triggered|gated|answered|closed|performed) (?:separately |directly |later )?by the mayor\b"
        r"|\bjon's (?:decision|call)\b|\bjon-gated\b|\bneeds (?:jon|the mayor|the owner|a human)\b"
        r"|\b(?:user|product|owner|comms|data)(?:/\w+)? decision\b|\bstanding decisions?\b"
        r"|\bbelongs to\b|\bds-side\b|\bdesign-side[- ]only\b",
    ),
    (
        "a deferral",
        r"\bdefer(?:red|s|ral|rals)?\b|\bfine to (?:defer|decline|skip)\b|\bnon-blocking\b"
        r"|\b(?:does|do) not block (?:this (?:pr|ac|bead|build|work|change)|on it)\b"
        r"|\boptional \(|\(optional\b|\bis optional\b|\boptional here\b"
        r"|\bheld (?:-|until|separately)|\b(?:is|are|intentionally) held\b"
        r"|\bblocked[- ](?:by|on)\b|\breported as blocked\b",
    ),
)
PERMIT_CUE_RES = tuple((label, re.compile(pattern)) for label, pattern in PERMIT_CUES)
PERMIT_CUE_EXAMPLES = (
    '"post-merge", "after merge", "next round", "follow-up bead", "out of scope", "not in this work", '
    '"separate bead", "do not touch", "mayor-owned", "the mayor checks", "Jon\'s decision", "deferred", "blocked on"'
)

# Requirement ids a requirements artifact defines. An id is a label made of
# capital letters, a hyphen and a number (AC-1, SCOPE-2, REQ-001, OQ-5, CON-1,
# OOS-3, TS-1, US-2), optionally with one lower-case letter (AC-8b). It counts
# as defined where it leads a list item, a heading, or a paragraph line, as in
# `- **AC-1.** ...`, `- AC-1: ...`, `### REQ-1 — ...`, `**AC-1 (item 1).**`,
# `REQ-001: ...`. A mention in running prose is not a definition. Labels
# without a hyphen (R1, G4, Q2) are not extracted: in the corpus the same
# shape names design rounds ("R10 — Settings") and findings.
#
# Only some defined ids are FORCED, that is, must have a coverage entry even
# when the artifact does not list them: those whose prefix states an
# obligation (acceptance criteria, scope items, requirements). An open
# question, an out-of-scope note or a user story carries a label too, and
# forcing an entry for it left `covered` as its only passing status.
# GC_BUILD_FORCED_ID_PREFIXES (comma-separated) replaces the list. An id
# defined under a heading that says the section is not a list of obligations
# (out of scope, non-goals, open questions, background, verified) is never
# forced. Ids the artifact lists under trace.upstream[].ids need an entry in
# any case (validate_coverage_completeness).
DEFAULT_FORCED_ID_PREFIXES = ("AC", "SCOPE", "REQ")
FORCED_ID_PREFIXES_ENV = "GC_BUILD_FORCED_ID_PREFIXES"
UNFORCED_HEADING_RE = re.compile(
    r"out[- ]of[- ]scope|not in scope|non[- ]?goals?|open questions?|background|verified", re.IGNORECASE
)
HEADING_RE = re.compile(r"^[ \t]*(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+")

# A conditional requirement ("If only the test is wrong: ...") whose condition
# does not hold is `not_applicable`. Nothing hands it off, so no hand-off cue
# can permit it; its permit is its own conditional clause, and the entry's
# rationale says why the condition is false. This is the one case where a
# requirement's own statement is its permit.
CONDITIONAL_CLAUSE_RE = re.compile(r"\b(?:if|unless|when|whenever|in case|provided that|as long as)\b")
MIN_CONDITION_RATIONALE_CHARS = 20
REQUIREMENT_ID_DEFINITION_RE = re.compile(
    r"^[ \t]*(?:>[ \t]*)*"
    r"(?:(?P<list>(?:[-*+]|\d{1,3}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?)|(?P<head>#{1,6}[ \t]+))?"
    r"(?P<open>\*\*|__|`)?"
    r"(?P<id>[A-Z][A-Z0-9]{0,11}-\d{1,4}[a-z]?)"
    r"(?P<after>.{0,3})"
)
REQUIREMENT_ID_NOT_A_LABEL_RE = re.compile(r"^[-'\u2019/\u2013\w]")
REQUIREMENT_ID_PLAIN_LABEL_END_RE = re.compile(r"^(?:[.:)](?:\s|\*|$)|\s*$|\s\(|\s[-\u2013\u2014]\s)")
REQUIREMENT_ID_PARAGRAPH_LABEL_END_RE = re.compile(r"^[.:](?:\s|$)")
FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")


class ValidationError(Exception):
    pass


YAML_ERROR_TYPES = (yaml.YAMLError,) if yaml is not None else ()
CLI_ERROR_TYPES = (OSError, UnicodeDecodeError, ValidationError) + YAML_ERROR_TYPES


@dataclass(frozen=True)
class BuildArtifact:
    schema_id: str
    front_matter: dict[str, Any]
    body: str
    upstream: list[dict[str, Any]]
    coverage: list[dict[str, Any]]


def validate_artifact_text(
    text: str,
    *,
    expected_schema: str = "",
    require_coverage_permits: bool = False,
    requirements_sources: list[tuple[str, str]] | None = None,
    requirements_hint: str = "",
    partial_coverage: str = "",
) -> BuildArtifact:
    """Validate one build artifact.

    require_coverage_permits turns on the coverage rules for this artifact;
    requirements_sources is the (label, text) list whose requirement ids must
    all be covered and whose text a permit may quote. The caller supplies it:
    an artifact never names its own requirements. requirements_hint says, for
    the error message, why the list is empty when it is. partial_coverage, when
    set, says why this artifact covers only part of the requirements (one item
    of a drain of several): the every-id rule is then not applied.
    """
    schema_id, front_matter, body = parse_front_matter(text)
    if expected_schema and schema_id != expected_schema:
        raise ValidationError(f"schema must be {expected_schema!r}, got {schema_id!r}")

    schema = load_schema(schema_id)
    validate_required_front_matter(front_matter, schema)
    validate_status(front_matter, schema)
    trace = validate_trace(front_matter)
    upstream = validate_upstream(trace)
    coverage = validate_coverage(trace, schema)
    validate_coverage_completeness(upstream, coverage)
    if require_coverage_permits:
        validate_requirements_coverage(
            front_matter, coverage, requirements_sources or [], requirements_hint, partial_coverage
        )
    validate_markdown_coverage(body, coverage)
    validate_required_sections(body, schema)
    return BuildArtifact(
        schema_id=schema_id,
        front_matter=front_matter,
        body=body,
        upstream=upstream,
        coverage=coverage,
    )


def parse_front_matter(text: str) -> tuple[str, dict[str, Any], str]:
    if yaml is None:
        raise ValidationError("PyYAML is required to parse build artifacts")
    match = FRONT_MATTER_RE.match(text)
    if not match:
        raise ValidationError("build artifact must start with YAML front matter")
    data = yaml.safe_load(match.group("front")) or {}
    if not isinstance(data, dict):
        raise ValidationError("build artifact front matter must be a mapping")
    schema_id = required_string(data, "schema")
    return schema_id, data, match.group("body")


def materialized_scope_root() -> Path | None:
    # A copy installed at <scope-root>/.gc/scripts/ (city or rig root) has no
    # schemas/ beside it, and nothing refreshes such copies on a repin, so a
    # sibling schemas/build there (if any) is an unmanaged snapshot.
    if SCRIPT_DIR.name == "scripts" and SCRIPT_DIR.parent.name == ".gc":
        return SCRIPT_DIR.parent.parent
    return None


def _pack_name(pack_dir: Path) -> str:
    try:
        text = (pack_dir / "pack.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if tomllib is None:  # pragma: no cover - Python < 3.11
        match = PACK_NAME_RE.search(text)
        return match.group(1) if match else ""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return ""
    pack = data.get("pack")
    name = pack.get("name", "") if isinstance(pack, dict) else ""
    return name if isinstance(name, str) else ""


def installed_pack_schema_root(scope_root: Path) -> Path | None:
    """Return <pack>/schemas/build for the gascity pack gc has installed for scope_root.

    gc resolves each pack import to a pinned cache directory and reports its
    formula layers (<pack>/formulas) via `gc formula list --json`; the base
    schemas ship beside them. Asking gc on every run keeps a materialized
    validator on the currently pinned schemas across repins. Returns None when
    gc is unavailable or reports no gascity pack; callers then fail closed.
    """
    gc = shutil.which("gc")
    if gc is None:
        return None
    try:
        proc = subprocess.run(
            [gc, "formula", "list", "--json"],
            cwd=scope_root,
            capture_output=True,
            text=True,
            timeout=INSTALLED_PACK_LOOKUP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    search_paths = data.get("search_paths") if isinstance(data, dict) else None
    if not isinstance(search_paths, list):
        return None
    found: Path | None = None
    for raw in search_paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        pack_dir = Path(raw.strip()).parent
        schema_dir = pack_dir / "schemas" / "build"
        if schema_dir.is_dir() and _pack_name(pack_dir) == BASE_PACK_NAME:
            # Search paths run lowest to highest priority; keep the last match
            # so a rig-level gascity import wins, as it does for formulas.
            found = schema_dir
    return found


def base_schema_root() -> Path:
    scope_root = materialized_scope_root()
    if scope_root is None:
        return SCHEMA_ROOT
    installed = installed_pack_schema_root(scope_root)
    if installed is not None:
        return installed
    # gc could not name the installed pack: fall back to whatever sits beside
    # the copy. When nothing does, every schema id stays unknown (fail closed).
    return SCHEMA_ROOT


def schema_roots() -> list[Path]:
    # Base root always first: a published base schema id resolves from the
    # base pack before any extra root is consulted, so extra roots can only
    # ADD new ids — they can never shadow or relax a published base schema
    # (REQUIREMENTS "Schema IDs are immutable compatibility contracts").
    # GC_BUILD_SCHEMA_ROOTS is os.pathsep-separated; missing dirs are skipped.
    roots = [base_schema_root()]
    for raw in os.environ.get("GC_BUILD_SCHEMA_ROOTS", "").split(os.pathsep):
        raw = raw.strip()
        if not raw:
            continue
        root = Path(raw)
        if root.is_dir():
            roots.append(root)
    return roots


def load_schema(schema_id: str) -> dict[str, Any]:
    if yaml is None:
        raise ValidationError("PyYAML is required to parse build schemas")
    roots = schema_roots()
    for root in roots:
        for path in sorted(root.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict) and raw.get("schema_id") == schema_id:
                validate_schema_definition(raw)
                return raw
    searched = os.pathsep.join(str(root) for root in roots)
    raise ValidationError(f"unknown build artifact schema {schema_id!r} (searched schema roots: {searched})")


def validate_schema_definition(schema: dict[str, Any]) -> None:
    schema_id = schema.get("schema_id", "<unknown>")
    fields = schema.get("required_front_matter", [])
    if not isinstance(fields, list):
        raise ValidationError(f"schema {schema_id}: required_front_matter must be a list")
    for field in fields:
        leaf = str(field).split(".")[-1].lower()
        if leaf in FORBIDDEN_REQUIRED_FIELD_NAMES:
            raise ValidationError(
                f"schema {schema_id}: base schemas must not require owner, stage-owner, persona, or role fields, got {field!r}"
            )


def validate_required_front_matter(front_matter: dict[str, Any], schema: dict[str, Any]) -> None:
    fields = schema.get("required_front_matter", [])
    if not isinstance(fields, list):
        raise ValidationError(f"schema {schema.get('schema_id', '<unknown>')}: required_front_matter must be a list")
    missing = [field for field in fields if get_path(front_matter, str(field)) is None]
    if missing:
        raise ValidationError(f"front matter missing required fields: {missing}")
    for field in fields:
        value = get_path(front_matter, str(field))
        if isinstance(value, str) and not value.strip():
            raise ValidationError(f"front matter field {field} must be non-empty")
    attempt = get_path(front_matter, "producer.attempt")
    if not isinstance(attempt, int) or attempt < 1:
        raise ValidationError("producer.attempt must be a positive integer")


def validate_status(front_matter: dict[str, Any], schema: dict[str, Any]) -> None:
    status = required_string(front_matter, "status")
    allowed = schema.get("allowed_statuses", [])
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise ValidationError(f"schema {schema.get('schema_id', '<unknown>')}: allowed_statuses must be strings")
    if status not in allowed:
        raise ValidationError(f"status must be one of {sorted(allowed)}, got {status!r}")


def validate_trace(front_matter: dict[str, Any]) -> dict[str, Any]:
    trace = front_matter.get("trace")
    if not isinstance(trace, dict):
        raise ValidationError("trace must be a mapping")
    if "upstream" not in trace:
        raise ValidationError("trace.upstream must be present")
    if "coverage" not in trace:
        raise ValidationError("trace.coverage must be present")
    if not isinstance(trace["upstream"], list):
        raise ValidationError("trace.upstream must be a list")
    if not isinstance(trace["coverage"], list):
        raise ValidationError("trace.coverage must be a list")
    return trace


def validate_upstream(trace: dict[str, Any]) -> list[dict[str, Any]]:
    upstream: list[dict[str, Any]] = []
    for index, raw in enumerate(trace["upstream"]):
        if not isinstance(raw, dict):
            raise ValidationError(f"trace.upstream[{index}] must be a mapping")
        path = required_string(raw, "path", prefix=f"trace.upstream[{index}]")
        hash_value = required_string(raw, "hash", prefix=f"trace.upstream[{index}]")
        validate_upstream_path(path, index)
        if ":" not in hash_value:
            raise ValidationError(f"trace.upstream[{index}].hash must include a hash or revision scheme")
        ids = raw.get("ids")
        if ids is not None:
            if not isinstance(ids, list) or not all(isinstance(item, str) and item.strip() for item in ids):
                raise ValidationError(f"trace.upstream[{index}].ids must be a list of non-empty strings")
        upstream.append(raw)
    return upstream


def validate_coverage_completeness(upstream: list[dict[str, Any]], coverage: list[dict[str, Any]]) -> None:
    covered_ids = {str(entry["id"]) for entry in coverage}
    missing = [
        item_id
        for entry in upstream
        for item_id in entry.get("ids") or []
        if str(item_id).strip() not in covered_ids
    ]
    if missing:
        raise ValidationError(f"coverage must account for every upstream ID, missing: {missing}")


def validate_upstream_path(path: str, index: int) -> None:
    parsed = Path(path)
    if not parsed.is_absolute() and ".." in parsed.parts:
        raise ValidationError(f"trace.upstream[{index}].path must not escape the artifact root")


def validate_coverage(trace: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = schema.get("coverage_statuses", [])
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise ValidationError(f"schema {schema.get('schema_id', '<unknown>')}: coverage_statuses must be strings")
    allowed_set = set(allowed)
    seen: set[str] = set()
    coverage: list[dict[str, Any]] = []
    for index, raw in enumerate(trace["coverage"]):
        if not isinstance(raw, dict):
            raise ValidationError(f"trace.coverage[{index}] must be a mapping")
        item_id = required_string(raw, "id", prefix=f"trace.coverage[{index}]")
        status = required_string(raw, "status", prefix=f"trace.coverage[{index}]")
        if item_id in seen:
            raise ValidationError(f"trace.coverage[{index}].id duplicates {item_id!r}")
        seen.add(item_id)
        if status not in allowed_set:
            raise ValidationError(f"trace.coverage[{index}].status must be one of {sorted(allowed_set)}, got {status!r}")
        if status != "covered":
            required_string(raw, "rationale", prefix=f"trace.coverage[{index}]")
        coverage.append(raw)
    return coverage


def normalize_permit_text(text: str) -> str:
    """Fold everything a faithful quote may differ in from its source: line
    wrapping, letter case, typographic quotes, dashes and ellipses, blockquote,
    heading and list markers, Markdown links (the link text is kept), emphasis
    and code marks, table pipes, and dashes that stand alone."""
    text = text.translate(PERMIT_FOLD_TABLE)
    text = PERMIT_LINK_RE.sub(r"\1", text)
    text = PERMIT_BLOCKQUOTE_RE.sub("", text)
    text = PERMIT_LINE_MARKER_RE.sub("", text)
    text = PERMIT_MARKDOWN_NOISE_RE.sub("", text).replace("|", " ")
    text = PERMIT_ELLIPSIS_RE.sub(" ... ", text)
    # A dash standing alone is punctuation or a list marker written inline
    # ("a - b", "- one - two"): the same quote with or without it.
    text = PERMIT_LONE_DASH_RE.sub(" ", f" {text} ")
    return " ".join(text.casefold().split())


def permit_cue(folded_quote: str) -> str:
    """Name the kind of hand-off a folded quote states, or "" when it states none."""
    for label, pattern in PERMIT_CUE_RES:
        if pattern.search(folded_quote):
            return label
    return ""


@dataclass(frozen=True)
class RequirementDefinition:
    id: str
    forced: bool  # must have a coverage entry
    block: str  # the definition's own text: its list item, paragraph or heading


def forced_id_prefixes() -> tuple[str, ...]:
    raw = os.environ.get(FORCED_ID_PREFIXES_ENV)
    if raw is None:
        return DEFAULT_FORCED_ID_PREFIXES
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _defines_requirement_id(line: str) -> str:
    match = REQUIREMENT_ID_DEFINITION_RE.match(line)
    if not match or REQUIREMENT_ID_NOT_A_LABEL_RE.match(match.group("after")):
        return ""
    after = match.group("after")
    if match.group("head") or match.group("open"):
        defined = True
    elif match.group("list"):
        defined = bool(REQUIREMENT_ID_PLAIN_LABEL_END_RE.match(after))
    else:
        defined = bool(REQUIREMENT_ID_PARAGRAPH_LABEL_END_RE.match(after))
    return match.group("id") if defined else ""


def requirement_definitions(text: str) -> list[RequirementDefinition]:
    """Every place a requirements artifact defines an id, in document order."""
    prefixes = forced_id_prefixes()
    lines = text.splitlines()
    definitions: list[RequirementDefinition] = []
    headings: list[tuple[int, str]] = []  # the headings the current line is under
    in_fence = False
    for index, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        item_id = _defines_requirement_id(line)
        if item_id:
            under_unforced_heading = any(UNFORCED_HEADING_RE.search(title) for _, title in headings)
            forced = item_id.rsplit("-", 1)[0] in prefixes and not under_unforced_heading
            definitions.append(RequirementDefinition(item_id, forced, _definition_block(lines, index)))
        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, heading.group(2)))
    return definitions


def _definition_block(lines: list[str], start: int) -> str:
    """The definition line and the lines that continue it: up to the next id
    definition, heading, sibling list item, or paragraph break."""
    first = lines[start]
    if HEADING_RE.match(first):
        return first
    indent = _indent(first)
    block = [first]
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            following = next((later for later in lines[index + 1 :] if later.strip()), "")
            # A blank line ends the block unless the item continues, indented, after it.
            if not following or _indent(following) <= indent or HEADING_RE.match(following):
                break
            index += 1
            continue
        if HEADING_RE.match(line) or FENCE_RE.match(line) or _defines_requirement_id(line):
            break
        if LIST_ITEM_RE.match(line) and _indent(line) <= indent:
            break
        block.append(line)
        index += 1
    return "\n".join(block)


def extract_requirement_ids(text: str) -> list[str]:
    """Requirement ids defined in a requirements artifact, in first-seen order."""
    ids: list[str] = []
    for definition in requirement_definitions(text):
        if definition.id not in ids:
            ids.append(definition.id)
    return ids


def forced_requirement_ids(text: str) -> list[str]:
    """The defined ids that must have a coverage entry, in first-seen order."""
    ids: list[str] = []
    for definition in requirement_definitions(text):
        if definition.forced and definition.id not in ids:
            ids.append(definition.id)
    return ids


def coverage_accounts_for(requirement_id: str, coverage_ids: set[str]) -> bool:
    """True when an entry is for this requirement: the id itself, or the id
    with a descriptive suffix (`REQ-4-ci-wiring`, `AC-2.local`)."""
    if requirement_id in coverage_ids:
        return True
    return any(
        covered.startswith(requirement_id) and covered[len(requirement_id)] in "-_.:/ "
        for covered in coverage_ids
        if len(covered) > len(requirement_id)
    )


def validate_requirements_coverage(
    front_matter: dict[str, Any],
    coverage: list[dict[str, Any]],
    requirements_sources: list[tuple[str, str]],
    requirements_hint: str = "",
    partial_coverage: str = "",
) -> None:
    """Both coverage rules, reported together so one repair pass can fix both."""
    if str(front_matter.get("status", "")).strip() in PERMIT_EXEMPT_ARTIFACT_STATUSES:
        return
    problems = [
        problem
        for problem in (
            # One work item of several delivers some of the requirements; the
            # requirements artifact does not say which, so the every-id rule
            # cannot be applied to it.
            "" if partial_coverage else missing_requirement_ids_problem(coverage, requirements_sources),
            coverage_permits_problem(coverage, requirements_sources, requirements_hint),
        )
        if problem
    ]
    if problems:
        raise ValidationError("\n".join(problems))


def missing_requirement_ids_problem(coverage: list[dict[str, Any]], requirements_sources: list[tuple[str, str]]) -> str:
    coverage_ids = {str(entry["id"]) for entry in coverage}
    missing_by_source: list[str] = []
    for label, text in requirements_sources:
        missing = [item for item in forced_requirement_ids(text) if not coverage_accounts_for(item, coverage_ids)]
        if missing:
            missing_by_source.append(f"{label}: {', '.join(missing)}")
    if not missing_by_source:
        return ""
    return (
        "requirement ids: coverage must account for every requirement id the requirements artifact defines "
        f"(labels starting {', '.join(forced_id_prefixes())}), whether or not this artifact lists it under trace.upstream[].ids. "
        "Add a trace.coverage entry and a coverage-table row for each id missing from "
        + "; ".join(missing_by_source)
    )


def coverage_permits_problem(
    coverage: list[dict[str, Any]],
    requirements_sources: list[tuple[str, str]],
    requirements_hint: str = "",
) -> str:
    open_entries = [entry for entry in coverage if str(entry["status"]) != "covered"]
    if not open_entries:
        return ""

    if not requirements_sources:
        # Nothing to check a quote against. Checking it against some other
        # text (the work item, the plan) would reject a correct quote and
        # accept a wrong one, so say what is missing instead.
        open_ids = ", ".join(str(entry["id"]) for entry in open_entries)
        return (
            f"coverage permits: {len(open_entries)} coverage entries are not 'covered' ({open_ids}) and each needs a permit quoting the "
            "requirements artifact, but the requirements path could not be resolved"
            + (f": {requirements_hint}" if requirements_hint else ": the workflow root must record gc.build.requirements_path or gc.var.requirements_path")
        )

    labels = [label for label, _ in requirements_sources]
    haystacks = [normalize_permit_text(text) for _, text in requirements_sources]
    definitions = [definition for _, text in requirements_sources for definition in requirement_definitions(text)]
    problems: list[str] = []
    for entry in open_entries:
        item_id = str(entry["id"])
        entry_status = str(entry["status"])
        where = f"trace.coverage[{item_id}] (status {entry_status!r})"
        permit = entry.get("permit")
        if permit is None or (isinstance(permit, str) and not permit.strip()):
            problems.append(
                f"{where}: missing permit. Add `permit: \"<the sentence in the requirements artifact that allows this, quoted word for word>\"`"
            )
            continue
        if not isinstance(permit, str):
            problems.append(f"{where}: permit must be a string quoting the requirements artifact")
            continue
        needle = PERMIT_EDGE_ELLIPSIS_RE.sub("", normalize_permit_text(permit))
        if len(needle) < MIN_PERMIT_CHARS:
            problems.append(
                f"{where}: permit is {len(needle)} characters; quote at least {MIN_PERMIT_CHARS} characters of the requirements text that allows this"
            )
            continue
        shown = " ".join(permit.split())
        shown = shown if len(shown) <= 120 else shown[:117] + "..."
        if not any(needle in haystack for haystack in haystacks):
            problems.append(
                f"{where}: permit text is not in the requirements artifact ({', '.join(labels)}): \"{shown}\". Quote the requirements word for word"
            )
            continue
        if permit_cue(needle):
            continue
        own_clause = any(
            needle in normalize_permit_text(definition.block)
            for definition in definitions
            if coverage_accounts_for(definition.id, {item_id})
        )
        if entry_status == "not_applicable" and own_clause and CONDITIONAL_CLAUSE_RE.search(needle):
            rationale = normalize_permit_text(str(entry.get("rationale") or ""))
            if len(rationale) < MIN_CONDITION_RATIONALE_CHARS or rationale == needle:
                problems.append(
                    f"{where}: the permit quotes the requirement's own condition, which is allowed for a conditional requirement that "
                    f"does not apply, but `rationale` must then state why the condition is false (at least {MIN_CONDITION_RATIONALE_CHARS} characters, in your own words)"
                )
            continue
        problems.append(
            f"{where}: the permit quotes the requirements, but the quoted text does not hand the requirement off: \"{shown}\". "
            f"A requirement's own statement is not a permit. Quote the words that say it is for later, for someone else, or not for this work (for example {PERMIT_CUE_EXAMPLES}); "
            "if the requirements do not say that, the requirement is yours to deliver. The one exception: a conditional requirement "
            "(\"If ...\", \"When ...\", \"Unless ...\") whose condition does not hold takes `status: not_applicable`, a permit quoting "
            "its own conditional clause, and a `rationale` saying why the condition is false"
        )
    if not problems:
        return ""
    return (
        "coverage permits: a requirement may be left at a status other than 'covered' only when the requirements artifact itself hands it off. "
        "For each entry below either do the work and mark it 'covered', quote the requirements text that hands it off in `permit`, "
        "or set the artifact `status: blocked` and stop for a decision.\n- "
        + "\n- ".join(problems)
    )


def read_requirements_sources(paths: list[Path]) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    for path in paths:
        try:
            sources.append((str(path), path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as exc:
            raise ValidationError(f"requirements artifact {path} cannot be read: {exc}") from exc
    return sources


def validate_markdown_coverage(body: str, coverage: list[dict[str, Any]]) -> None:
    expected = {str(item["id"]): str(item["status"]) for item in coverage}
    if not expected:
        return
    actual = parse_markdown_coverage(body)
    if not actual:
        raise ValidationError("markdown coverage matrix is missing")
    if actual != expected:
        raise ValidationError(f"markdown coverage matrix must match YAML coverage, got {actual!r}, expected {expected!r}")


def parse_markdown_coverage(body: str) -> dict[str, str]:
    coverage: dict[str, str] = {}
    lines = body.splitlines()
    index = 0
    while index < len(lines):
        cells = split_table_row(lines[index])
        header = [cell.lower() for cell in cells]
        if header and "id" in header and "status" in header:
            id_index = header.index("id")
            status_index = header.index("status")
            index += 1
            if index < len(lines) and is_separator_row(lines[index]):
                index += 1
            while index < len(lines):
                row = split_table_row(lines[index])
                if not row or len(row) <= max(id_index, status_index):
                    break
                item_id = clean_table_cell(row[id_index])
                status = clean_table_cell(row[status_index])
                if item_id and status:
                    coverage[item_id] = status
                index += 1
            continue
        index += 1
    return coverage


def split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [clean_table_cell(cell) for cell in stripped.strip("|").split("|")]


def is_separator_row(line: str) -> bool:
    cells = split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def clean_table_cell(value: str) -> str:
    return value.strip().strip("`").strip()


def validate_required_sections(body: str, schema: dict[str, Any]) -> None:
    required = schema.get("required_sections", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValidationError(f"schema {schema.get('schema_id', '<unknown>')}: required_sections must be strings")
    positions: list[tuple[str, int]] = []
    for section in required:
        match = re.search(rf"^##\s+{re.escape(section)}\s*$", body, re.MULTILINE)
        if not match:
            raise ValidationError(f"missing required body section {section!r}")
        positions.append((section, match.start()))
    for (left_name, left_pos), (right_name, right_pos) in zip(positions, positions[1:]):
        if left_pos >= right_pos:
            raise ValidationError(f"body section {left_name!r} must appear before {right_name!r}")


def required_string(data: dict[str, Any], key: str, *, prefix: str = "") -> str:
    value = data.get(key)
    field = f"{prefix}.{key}" if prefix else key
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    return value.strip()


def get_path(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a gc build artifact")
    parser.add_argument("--schema", required=True, help="Expected schema id")
    parser.add_argument("--path", required=True, type=Path, help="Artifact markdown path")
    parser.add_argument(
        "--require-coverage-permits",
        action="store_true",
        help="Reject coverage entries that are not 'covered' unless they carry a permit quoting --requirements",
    )
    parser.add_argument(
        "--requirements",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="Requirements artifact whose ids must all be covered and whose text a permit may quote (repeatable)",
    )
    parser.add_argument(
        "--partial-coverage",
        default="",
        metavar="WHY",
        help="This artifact covers only part of the requirements (for example 'item 2 of 3'): do not require every requirement id",
    )
    parser.add_argument(
        "--requirements-hint",
        default="",
        metavar="TEXT",
        help="Shown when a permit needs checking and no --requirements was resolved: why, and how to record the path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        artifact = validate_artifact_text(
            args.path.read_text(encoding="utf-8"),
            expected_schema=args.schema,
            require_coverage_permits=args.require_coverage_permits,
            requirements_sources=read_requirements_sources(args.requirements) if args.require_coverage_permits else None,
            requirements_hint=args.requirements_hint,
            partial_coverage=args.partial_coverage,
        )
    except CLI_ERROR_TYPES as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "schema": artifact.schema_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
