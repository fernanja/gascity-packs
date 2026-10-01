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
# may not leave a requirement at any status other than "covered" unless the
# coverage entry carries a `permit`: a word-for-word quote of the requirements
# text that allows it. An artifact whose own status already says the work is
# not approved needs no permits: it stops the build by itself.
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
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)
PERMIT_BLOCKQUOTE_RE = re.compile(r"(?m)^[ \t]*>+[ \t]?")
PERMIT_MARKDOWN_NOISE_RE = re.compile(r"[`*]")


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
) -> BuildArtifact:
    """Validate one build artifact.

    require_coverage_permits turns on the permit rule for this artifact;
    requirements_sources is the (label, text) list a permit may quote from. The
    caller supplies it: an artifact never names its own permit source.
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
        validate_coverage_permits(front_matter, coverage, requirements_sources or [])
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
    """Fold layout so a quote survives re-wrapping: whitespace runs, blockquote
    markers, Markdown emphasis/code marks, and typographic quotes and dashes."""
    text = text.translate(PERMIT_FOLD_TABLE)
    text = PERMIT_BLOCKQUOTE_RE.sub("", text)
    text = PERMIT_MARKDOWN_NOISE_RE.sub("", text)
    return " ".join(text.split())


def validate_coverage_permits(
    front_matter: dict[str, Any],
    coverage: list[dict[str, Any]],
    requirements_sources: list[tuple[str, str]],
) -> None:
    status = str(front_matter.get("status", "")).strip()
    if status in PERMIT_EXEMPT_ARTIFACT_STATUSES:
        return
    open_entries = [entry for entry in coverage if str(entry["status"]) != "covered"]
    if not open_entries:
        return

    labels = [label for label, _ in requirements_sources]
    haystacks = [normalize_permit_text(text) for _, text in requirements_sources]
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
        needle = normalize_permit_text(permit)
        if len(needle) < MIN_PERMIT_CHARS:
            problems.append(
                f"{where}: permit is {len(needle)} characters; quote at least {MIN_PERMIT_CHARS} characters of the requirements text that allows this"
            )
            continue
        if not haystacks:
            problems.append(
                f"{where}: no requirements artifact was supplied to check the permit against (the workflow root must record gc.build.requirements_path or gc.var.requirements_path)"
            )
            continue
        if not any(needle in haystack for haystack in haystacks):
            shown = needle if len(needle) <= 120 else needle[:117] + "..."
            problems.append(
                f"{where}: permit text is not in the requirements artifact ({', '.join(labels)}): \"{shown}\". Quote the requirements word for word"
            )
    if problems:
        raise ValidationError(
            "coverage permits: a requirement may be left at a status other than 'covered' only when the requirements artifact itself allows it. "
            "For each entry below either do the work and mark it 'covered', quote the permitting requirements text in `permit`, "
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
        help="Requirements artifact a permit may quote from (repeatable)",
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
        )
    except CLI_ERROR_TYPES as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "schema": artifact.schema_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
