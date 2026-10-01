from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
import io

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "assets" / "scripts"))

import validate_context_bundle as context_validator
import validate_build_artifact as build_artifact_validator
import validate_verdict_report as verdict_validator


class ContextBundleValidatorTests(unittest.TestCase):
    def test_valid_context_bundle_accepts_only_name_path_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            subject = root / "requirements.md"
            subject.write_text("# Requirements\n", encoding="utf-8")
            bundle = root / "context.yaml"
            bundle.write_text(
                "items:\n"
                "  - name: Requirements\n"
                "    path: requirements.md\n"
                "    description: Product requirements.\n",
                encoding="utf-8",
            )

            result = context_validator.validate_bundle(bundle, allowed_roots=[root])

            self.assertEqual([item.name for item in result.items], ["Requirements"])
            self.assertEqual(result.items[0].resolved_path, subject.resolve())

    def test_context_bundle_rejects_unknown_item_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "requirements.md").write_text("# Requirements\n", encoding="utf-8")
            bundle = root / "context.yaml"
            bundle.write_text(
                "items:\n"
                "  - name: Requirements\n"
                "    path: requirements.md\n"
                "    description: Product requirements.\n"
                "    inline: no\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(context_validator.ValidationError, "unknown fields"):
                context_validator.validate_bundle(bundle, allowed_roots=[root])

    def test_context_bundle_rejects_missing_files_and_symlink_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            outside = pathlib.Path(tmp) / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")
            link = root / "link.md"
            link.symlink_to(outside)
            bundle = root / "context.yaml"
            bundle.write_text(
                "items:\n"
                "  - name: Link\n"
                "    path: link.md\n"
                "    description: Escaping symlink.\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(context_validator.ValidationError, "outside allowed roots"):
                context_validator.validate_bundle(bundle, allowed_roots=[root / "allowed"])

            bundle.write_text(
                "items:\n"
                "  - name: Missing\n"
                "    path: missing.md\n"
                "    description: Missing file.\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(context_validator.ValidationError, "does not exist"):
                context_validator.validate_bundle(bundle, allowed_roots=[root])

    def test_context_bundle_rejects_binary_and_secret_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            binary = root / "blob.bin"
            binary.write_bytes(b"abc\x00def")
            bundle = root / "context.yaml"
            bundle.write_text(
                "items:\n"
                "  - name: Blob\n"
                "    path: blob.bin\n"
                "    description: Binary file.\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(context_validator.ValidationError, "binary"):
                context_validator.validate_bundle(bundle, allowed_roots=[root])

            secret = root / ".env"
            secret.write_text("TOKEN=secret\n", encoding="utf-8")
            bundle.write_text(
                "items:\n"
                "  - name: Secret\n"
                "    path: .env\n"
                "    description: Secret file.\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(context_validator.ValidationError, "secret"):
                context_validator.validate_bundle(bundle, allowed_roots=[root])

            for filename in (".env.production", "private.pem", ".ssh/config", ".git/config", "cookies.txt"):
                secret = root / filename
                secret.parent.mkdir(parents=True, exist_ok=True)
                secret.write_text("secret\n", encoding="utf-8")
                bundle.write_text(
                    "items:\n"
                    "  - name: Secret\n"
                    f"    path: {filename}\n"
                    "    description: Secret file.\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(context_validator.ValidationError, "secret"):
                    context_validator.validate_bundle(bundle, allowed_roots=[root])

    def test_context_bundle_accepts_benign_files_under_secret_named_ancestors(self) -> None:
        with tempfile.TemporaryDirectory(prefix="secret-project-") as tmp:
            root = pathlib.Path(tmp)
            subject = root / "requirements.md"
            subject.write_text("# Requirements\n", encoding="utf-8")
            bundle = root / "context.yaml"
            bundle.write_text(
                "items:\n"
                "  - name: Requirements\n"
                "    path: requirements.md\n"
                "    description: Product requirements.\n",
                encoding="utf-8",
            )

            result = context_validator.validate_bundle(bundle, allowed_roots=[root])

            self.assertEqual(result.items[0].resolved_path, subject.resolve())

    def test_context_bundle_rejects_secret_named_relative_directories(self) -> None:
        for dirname in (
            "secrets",
            "credentials",
            "tokens",
            "cookies",
            "vault-secrets",
            "aws-credentials",
            "oauth-tokens",
            "auth-cookies",
            "id_rsa_backup",
            "config/my-secret-store",
        ):
            with self.subTest(dirname=dirname), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                subject = root / dirname / "config.yaml"
                subject.parent.mkdir(parents=True)
                subject.write_text("token: value\n", encoding="utf-8")
                bundle = root / "context.yaml"
                bundle.write_text(
                    "items:\n"
                    "  - name: Secret config\n"
                    f"    path: {dirname}/config.yaml\n"
                    "    description: Secret material.\n",
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(context_validator.ValidationError, "secret"):
                    context_validator.validate_bundle(bundle, allowed_roots=[root])

    def test_context_bundle_rejects_symlink_to_secret_named_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            subject = root / "secrets" / "config.yaml"
            subject.parent.mkdir()
            subject.write_text("token: value\n", encoding="utf-8")
            link = root / "context.md"
            link.symlink_to(subject)
            bundle = root / "context.yaml"
            bundle.write_text(
                "items:\n"
                "  - name: Context\n"
                "    path: context.md\n"
                "    description: Benign-looking symlink.\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(context_validator.ValidationError, "secret"):
                context_validator.validate_bundle(bundle, allowed_roots=[root])

    def test_context_bundle_cli_reports_malformed_yaml_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = pathlib.Path(tmp) / "context.yaml"
            bundle.write_text("items:\n  - name: [\n", encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                code = context_validator.main([str(bundle)])

            self.assertEqual(code, 1)
            self.assertIn("error:", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())


class BuildArtifactValidatorTests(unittest.TestCase):
    SCHEMA_SECTIONS = {
        "gc.build.requirements.v1": [
            "Problem Statement",
            "W6H",
            "User Stories",
            "Technical Stories",
            "Behavior Requirements",
            "Example Mapping",
            "Acceptance Criteria",
            "Out Of Scope",
            "Open Questions",
        ],
        "gc.build.plan.v1": [
            "Summary",
            "Current System",
            "Proposed Implementation",
            "Non-Goals",
            "Verification",
        ],
        "gc.build.decomposition.v1": [
            "Summary",
            "Selected Downstream Formulas",
            "Implementation Convoy",
            "Work Items",
        ],
        "gc.build.implementation-summary.v1": [
            "Summary",
            "Intended Behavior",
            "Changed Files",
            "Verification",
            "Remaining Risks",
        ],
        "gc.build.review.v1": [
            "Verdict",
            "Findings",
            "Verification",
        ],
        "gc.build.final-report.v1": [
            "Summary",
            "Outcome",
            "Artifacts",
            "Remaining Risks",
        ],
    }
    SCHEMA_STATUS = {
        "gc.build.requirements.v1": "approved",
        "gc.build.plan.v1": "approved",
        "gc.build.decomposition.v1": "approved",
        "gc.build.implementation-summary.v1": "approved",
        "gc.build.review.v1": "approved",
        "gc.build.final-report.v1": "approved",
    }
    SCHEMA_FILES = {
        "gc.build.requirements.v1": "requirements.v1.yaml",
        "gc.build.plan.v1": "plan.v1.yaml",
        "gc.build.decomposition.v1": "decomposition.v1.yaml",
        "gc.build.implementation-summary.v1": "implementation-summary.v1.yaml",
        "gc.build.review.v1": "review.v1.yaml",
        "gc.build.final-report.v1": "final-report.v1.yaml",
    }
    SCHEMA_ROOT = pathlib.Path(__file__).resolve().parents[1] / "schemas" / "build"
    VALIDATOR_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "assets" / "scripts" / "validate_build_artifact.py"

    def valid_artifact(self, schema: str = "gc.build.requirements.v1") -> str:
        sections = []
        for section in self.SCHEMA_SECTIONS[schema]:
            content = f"{section} content."
            if section in {"Example Mapping", "Summary", "Verdict"}:
                content += (
                    "\n\n| ID | Status |\n"
                    "| --- | --- |\n"
                    "| GC-METH-001 | covered |\n"
                    "| GC-METH-012 | deferred |"
                )
            sections.append(f"## {section}\n\n{content}")
        body = "\n\n".join(sections)
        return f"""---
schema: {schema}
workflow:
  id: build-20260609-001
  formula: build-basic
methodology:
  pack: gascity
  name: build-basic
producer:
  formula: planning-base
  stage: requirements
  attempt: 1
status: {self.SCHEMA_STATUS[schema]}
trace:
  upstream:
    - path: requirements.after.md
      hash: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  coverage:
    - id: GC-METH-001
      status: covered
    - id: GC-METH-012
      status: deferred
      rationale: Derived-pack compatibility is verified by a later work item.
---

{body}
"""

    def test_build_artifact_accepts_valid_minimal_artifacts_for_all_base_schemas(self) -> None:
        for schema in self.SCHEMA_SECTIONS:
            with self.subTest(schema=schema):
                artifact = build_artifact_validator.validate_artifact_text(
                    self.valid_artifact(schema),
                    expected_schema=schema,
                )

                self.assertEqual(artifact.schema_id, schema)
                self.assertEqual([entry["id"] for entry in artifact.coverage], ["GC-METH-001", "GC-METH-012"])

    def test_build_artifact_rejects_missing_front_matter_and_wrong_schema(self) -> None:
        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "front matter"):
            build_artifact_validator.validate_artifact_text("# Missing front matter\n", expected_schema="gc.build.requirements.v1")

        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "schema"):
            build_artifact_validator.validate_artifact_text(
                self.valid_artifact("gc.build.plan.v1"),
                expected_schema="gc.build.requirements.v1",
            )

    def test_build_artifact_rejects_missing_upstream_hash(self) -> None:
        text = self.valid_artifact().replace("      hash: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", "")

        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "hash"):
            build_artifact_validator.validate_artifact_text(text, expected_schema="gc.build.requirements.v1")

    def test_build_artifact_rejects_invalid_coverage_status_and_missing_rationale(self) -> None:
        invalid_status = self.valid_artifact().replace("status: deferred", "status: waiting", 1)
        missing_rationale = self.valid_artifact().replace(
            "      rationale: Derived-pack compatibility is verified by a later work item.\n",
            "",
        )

        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "coverage"):
            build_artifact_validator.validate_artifact_text(invalid_status, expected_schema="gc.build.requirements.v1")
        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "rationale"):
            build_artifact_validator.validate_artifact_text(missing_rationale, expected_schema="gc.build.requirements.v1")

    def test_build_artifact_rejects_markdown_yaml_coverage_mismatch(self) -> None:
        text = self.valid_artifact().replace("| GC-METH-012 | deferred |", "| GC-METH-012 | covered |")

        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "markdown coverage"):
            build_artifact_validator.validate_artifact_text(text, expected_schema="gc.build.requirements.v1")

    def test_build_artifact_validator_and_schema_files_are_present(self) -> None:
        self.assertTrue(self.VALIDATOR_SCRIPT.is_file(), f"missing {self.VALIDATOR_SCRIPT}")
        for schema_id, filename in self.SCHEMA_FILES.items():
            with self.subTest(schema=schema_id):
                self.assertTrue((self.SCHEMA_ROOT / filename).is_file(), f"missing {self.SCHEMA_ROOT / filename}")

    def test_build_artifact_schemas_keep_producer_metadata_neutral(self) -> None:
        for schema_id in self.SCHEMA_FILES:
            with self.subTest(schema=schema_id):
                schema = build_artifact_validator.load_schema(schema_id)

                leaves = {str(field).split(".")[-1].lower() for field in schema["required_front_matter"]}
                self.assertFalse(leaves & build_artifact_validator.FORBIDDEN_REQUIRED_FIELD_NAMES)

    def test_build_artifact_rejects_schema_requiring_role_fields(self) -> None:
        for field in ("owner", "stage-owner", "persona", "producer.role"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(build_artifact_validator.ValidationError, "must not require"):
                    build_artifact_validator.validate_schema_definition(
                        {"schema_id": "gc.build.requirements.v1", "required_front_matter": ["schema", field]}
                    )

    def test_build_artifact_statuses_follow_base_approval_states(self) -> None:
        review_questions = self.valid_artifact("gc.build.review.v1").replace(
            "\nstatus: approved\n", "\nstatus: questions\n"
        )
        summary_complete = self.valid_artifact("gc.build.implementation-summary.v1").replace(
            "\nstatus: approved\n", "\nstatus: complete\n"
        )

        artifact = build_artifact_validator.validate_artifact_text(
            review_questions, expected_schema="gc.build.review.v1"
        )
        self.assertEqual(artifact.front_matter["status"], "questions")
        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "status"):
            build_artifact_validator.validate_artifact_text(
                summary_complete, expected_schema="gc.build.implementation-summary.v1"
            )

    def test_build_artifact_requires_coverage_for_declared_upstream_ids(self) -> None:
        declared = self.valid_artifact().replace(
            "      hash: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
            "      hash: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            "      ids:\n"
            "        - GC-METH-001\n"
            "        - GC-METH-012\n",
        )
        missing = declared.replace("        - GC-METH-012\n", "        - GC-METH-012\n        - GC-METH-099\n")

        artifact = build_artifact_validator.validate_artifact_text(declared, expected_schema="gc.build.requirements.v1")
        self.assertEqual([entry["id"] for entry in artifact.coverage], ["GC-METH-001", "GC-METH-012"])
        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "GC-METH-099"):
            build_artifact_validator.validate_artifact_text(missing, expected_schema="gc.build.requirements.v1")

    def test_build_artifact_cli_reports_errors_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / "requirements.md"
            report.write_text("---\nschema: [\n---\n", encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                code = build_artifact_validator.main(
                    ["--schema", "gc.build.requirements.v1", "--path", str(report)]
                )

            self.assertEqual(code, 1)
            self.assertIn("error:", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())


class CoveragePermitTests(unittest.TestCase):
    """gc-gdyaz: a requirement may be left uncovered only with a permit that
    quotes the requirements artifact."""

    REQUIREMENTS = (
        "# Requirements\n\n"
        "## Acceptance Criteria\n\n"
        "- **AC-1:** the new guard rejects a team-less item.\n"
        "- **AC-2:** `make preflight-fast` exits 0 and the pull request's\n"
        "  checks are green.\n"
        "- **AC-3:** after merge, the Dependabot alerts for these packages auto-close.\n"
        "  The mayor checks this *post-merge*; it is not a worker AC.\n\n"
        "## Out Of Scope\n\n"
        "> Repairing production rows is Jon\u2019s decision \u2014 the database is\n"
        "> never edited directly.\n"
    )
    PERMIT_AC3 = "The mayor checks this post-merge; it is not a worker AC."
    SECTIONS = {
        "gc.build.plan.v1": ["Summary", "Current System", "Proposed Implementation", "Non-Goals", "Verification"],
        "gc.build.implementation-summary.v1": [
            "Summary",
            "Intended Behavior",
            "Changed Files",
            "Verification",
            "Remaining Risks",
        ],
    }

    def artifact(
        self,
        schema: str = "gc.build.plan.v1",
        *,
        ac3_status: str = "out_of_scope",
        permit: str | None = None,
        status: str = "approved",
        extra_entry: str = "",
        extra_row: str = "",
    ) -> str:
        permit_line = f"      permit: {json.dumps(permit)}\n" if permit is not None else ""
        rationale_line = "      rationale: Checked by the mayor after merge.\n" if ac3_status != "covered" else ""
        body = "\n\n".join(
            f"## {section}\n\n{section} content."
            + (
                "\n\n| ID | Status |\n| --- | --- |\n| AC-1 | covered |\n| AC-2 | covered |\n"
                f"| AC-3 | {ac3_status} |{extra_row}"
                if section == "Summary"
                else ""
            )
            for section in self.SECTIONS[schema]
        )
        return (
            "---\n"
            f"schema: {schema}\n"
            "workflow:\n  id: gcas-root\n  formula: build-from-plan\n"
            "methodology:\n  pack: gascity\n  name: build-from-plan\n"
            "producer:\n  formula: build-from-plan\n  stage: plan\n  attempt: 1\n"
            f"status: {status}\n"
            "trace:\n"
            "  upstream:\n"
            "    - path: requirements.md\n"
            "      hash: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            "      ids: [AC-1, AC-2, AC-3]\n"
            "  coverage:\n"
            "    - id: AC-1\n      status: covered\n"
            "    - id: AC-2\n      status: covered\n"
            f"    - id: AC-3\n      status: {ac3_status}\n"
            f"{rationale_line}{permit_line}{extra_entry}"
            "---\n\n"
            f"{body}\n"
        )

    def validate(self, text: str, schema: str = "gc.build.plan.v1", requirements: str | None = None):
        sources = [("requirements.md", self.REQUIREMENTS if requirements is None else requirements)]
        return build_artifact_validator.validate_artifact_text(
            text,
            expected_schema=schema,
            require_coverage_permits=True,
            requirements_sources=sources,
        )

    def test_uncovered_requirement_with_quoted_permit_is_accepted_for_plan_and_summary(self) -> None:
        for schema in self.SECTIONS:
            for status in ("out_of_scope", "deferred", "blocked", "not_applicable", "superseded"):
                with self.subTest(schema=schema, status=status):
                    artifact = self.validate(self.artifact(schema, ac3_status=status, permit=self.PERMIT_AC3), schema)
                    self.assertEqual(artifact.coverage[2]["permit"], self.PERMIT_AC3)

    def test_fully_covered_artifact_needs_no_permit(self) -> None:
        for schema in self.SECTIONS:
            with self.subTest(schema=schema):
                self.validate(self.artifact(schema, ac3_status="covered"), schema)

    def test_uncovered_requirement_without_permit_is_rejected_and_named(self) -> None:
        for schema in self.SECTIONS:
            for status in ("deferred", "blocked", "out_of_scope", "not_applicable", "superseded"):
                with self.subTest(schema=schema, status=status):
                    with self.assertRaises(build_artifact_validator.ValidationError) as caught:
                        self.validate(self.artifact(schema, ac3_status=status), schema)
                    message = str(caught.exception)
                    self.assertIn("trace.coverage[AC-3]", message)
                    self.assertIn(f"status '{status}'", message)
                    self.assertIn("missing permit", message)
                    self.assertIn("status: blocked", message)

    def test_permit_must_quote_text_that_is_in_the_requirements(self) -> None:
        invented = "AC-3 can wait until the publish stage has opened the pull request."
        with self.assertRaises(build_artifact_validator.ValidationError) as caught:
            self.validate(self.artifact(ac3_status="deferred", permit=invented))
        message = str(caught.exception)
        self.assertIn("trace.coverage[AC-3]", message)
        self.assertIn("permit text is not in the requirements artifact (requirements.md)", message)
        self.assertIn(invented, message)

    def test_permit_match_ignores_wrapping_emphasis_and_typographic_marks(self) -> None:
        # The requirements wrap the sentence, emphasise a word and use a curly
        # apostrophe, an em dash and blockquote markers; the quote has none.
        for permit in (
            "The mayor checks this post-merge;   it is not a\n worker AC.",
            "Repairing production rows is Jon's decision - the database is never edited directly.",
            "`make preflight-fast` exits 0 and the pull request's checks are green.",
        ):
            with self.subTest(permit=permit):
                self.validate(self.artifact(ac3_status="deferred", permit=permit))

    def test_permit_too_short_or_not_a_string_is_rejected(self) -> None:
        with self.assertRaisesRegex(build_artifact_validator.ValidationError, r"permit is 12 characters; quote at least 20"):
            self.validate(self.artifact(ac3_status="deferred", permit="Out Of Scope"))
        not_a_string = self.artifact(ac3_status="deferred").replace(
            "      rationale: Checked by the mayor after merge.\n",
            "      rationale: Checked by the mayor after merge.\n      permit: [requirements.md]\n",
        )
        with self.assertRaisesRegex(build_artifact_validator.ValidationError, r"AC-3.*permit must be a string"):
            self.validate(not_a_string)

    def test_every_unpermitted_entry_is_reported_in_one_error(self) -> None:
        text = self.artifact(
            ac3_status="deferred",
            extra_entry=(
                "    - id: OQ-1\n      status: deferred\n      rationale: Screenshots were not taken this session.\n"
            ),
            extra_row="\n| OQ-1 | deferred |",
        )
        with self.assertRaises(build_artifact_validator.ValidationError) as caught:
            self.validate(text)
        message = str(caught.exception)
        self.assertIn("trace.coverage[AC-3]", message)
        self.assertIn("trace.coverage[OQ-1]", message)

    def test_artifact_that_is_not_approved_needs_no_permits(self) -> None:
        for status in ("blocked", "superseded"):
            with self.subTest(status=status):
                self.validate(
                    self.artifact("gc.build.implementation-summary.v1", ac3_status="blocked", status=status),
                    "gc.build.implementation-summary.v1",
                )
        for status in ("blocked", "questions", "changes_required", "superseded"):
            with self.subTest(status=status):
                self.validate(self.artifact(ac3_status="deferred", status=status))
        # A draft is still on its way to approval, so it is held to the rule.
        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "missing permit"):
            self.validate(self.artifact(ac3_status="deferred", status="draft"))

    def test_permit_cannot_be_checked_without_a_requirements_artifact(self) -> None:
        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "no requirements artifact was supplied"):
            build_artifact_validator.validate_artifact_text(
                self.artifact(ac3_status="deferred", permit=self.PERMIT_AC3),
                expected_schema="gc.build.plan.v1",
                require_coverage_permits=True,
                requirements_sources=[],
            )

    def test_permit_rule_is_off_unless_the_stage_opts_in(self) -> None:
        artifact = build_artifact_validator.validate_artifact_text(
            self.artifact(ac3_status="deferred"), expected_schema="gc.build.plan.v1"
        )
        self.assertEqual(artifact.coverage[2]["status"], "deferred")

    def test_listed_requirement_ids_still_need_a_coverage_entry(self) -> None:
        text = self.artifact(ac3_status="covered").replace("ids: [AC-1, AC-2, AC-3]", "ids: [AC-1, AC-2, AC-3, AC-4]")
        with self.assertRaisesRegex(build_artifact_validator.ValidationError, r"missing: \['AC-4'\]"):
            self.validate(text)

    def test_cli_checks_permits_against_the_given_requirements_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            requirements = root / "requirements.md"
            requirements.write_text(self.REQUIREMENTS, encoding="utf-8")
            plan = root / "implementation-plan.md"
            base = ["--schema", "gc.build.plan.v1", "--path", str(plan)]
            permits = base + ["--require-coverage-permits", "--requirements", str(requirements)]

            plan.write_text(self.artifact(ac3_status="deferred"), encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                self.assertEqual(build_artifact_validator.main(base), 0)
                self.assertEqual(build_artifact_validator.main(permits), 1)
            self.assertIn("trace.coverage[AC-3]", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

            plan.write_text(self.artifact(ac3_status="deferred", permit=self.PERMIT_AC3), encoding="utf-8")
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(build_artifact_validator.main(permits), 0)

            stderr = io.StringIO()
            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                code = build_artifact_validator.main(
                    base + ["--require-coverage-permits", "--requirements", str(root / "missing.md")]
                )
            self.assertEqual(code, 1)
            self.assertIn("cannot be read", stderr.getvalue())


class VerdictReportValidatorTests(unittest.TestCase):
    def test_verdict_report_accepts_pass_and_fail_reports(self) -> None:
        pass_report = """---
schema: gc.verdict-report.v1
kind: review
verdict: pass
severity: none
findings: []
---

No issues found.
"""
        fail_report = """---
schema: gc.verdict-report.v1
kind: gap-analysis
verdict: fail
severity: major
findings:
  - id: gap-001
    severity: major
    title: Missing restart test
    evidence: No test covers restart.
    required_fix: Add restart coverage.
---

Failure details.
"""

        self.assertEqual(verdict_validator.validate_report_text(pass_report).verdict, "pass")
        self.assertEqual(verdict_validator.validate_report_text(fail_report).severity, "major")

    def test_verdict_report_rejects_bad_schema_and_unstructured_failures(self) -> None:
        bad_schema = """---
schema: other
kind: review
verdict: pass
severity: none
findings: []
---
"""
        no_findings = """---
schema: gc.verdict-report.v1
kind: review
verdict: fail
severity: major
findings: []
---
"""

        with self.assertRaisesRegex(verdict_validator.ValidationError, "schema"):
            verdict_validator.validate_report_text(bad_schema)
        with self.assertRaisesRegex(verdict_validator.ValidationError, "findings"):
            verdict_validator.validate_report_text(no_findings)

    def test_verdict_report_rejects_severity_that_is_not_max_finding_severity(self) -> None:
        report = """---
schema: gc.verdict-report.v1
kind: review
verdict: fail
severity: minor
findings:
  - id: rev-001
    severity: blocker
    title: Unsafe publish
    evidence: Publish can mutate protected branch.
    required_fix: Block protected branches.
---
"""

        with self.assertRaisesRegex(verdict_validator.ValidationError, "maximum"):
            verdict_validator.validate_report_text(report)

    def test_verdict_report_rejects_non_string_finding_fields(self) -> None:
        field_values = {
            "id": "0",
            "severity": "0",
            "title": "{}",
            "evidence": "null",
            "required_fix": "[]",
        }
        for field, yaml_value in field_values.items():
            with self.subTest(field=field):
                report = f"""---
schema: gc.verdict-report.v1
kind: review
verdict: fail
severity: major
findings:
  - id: rev-001
    severity: major
    title: Missing test
    evidence: No test.
    required_fix: Add one.
    {field}: {yaml_value}
---
"""

                with self.assertRaisesRegex(verdict_validator.ValidationError, "missing fields"):
                    verdict_validator.validate_report_text(report)

    def test_verdict_report_cli_reports_malformed_yaml_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / "review.md"
            report.write_text("---\nschema: [\n---\n", encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                code = verdict_validator.main([str(report), "--kind", "review"])

            self.assertEqual(code, 1)
            self.assertIn("error:", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_verdict_report_cli_reports_invalid_utf8_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / "review.md"
            report.write_bytes(b"\xff\xfe---\n")
            stderr = io.StringIO()

            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                code = verdict_validator.main([str(report), "--kind", "review"])

            self.assertEqual(code, 1)
            self.assertIn("error:", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())


class BuildArtifactSchemaRootsTests(unittest.TestCase):
    def _write_schema(self, root: pathlib.Path, name: str, schema_id: str) -> None:
        (root / name).write_text(
            "schema_id: " + schema_id + "\n"
            "required_front_matter: [schema, status, trace]\n"
            "allowed_statuses: [approved]\n"
            "coverage_statuses: [covered]\n"
            "required_sections: []\n",
            encoding="utf-8",
        )

    def test_extra_root_resolves_new_schema_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write_schema(root, "custom.v1.yaml", "acme.build.custom.v1")
            with mock.patch.dict(os.environ, {"GC_BUILD_SCHEMA_ROOTS": str(root)}):
                schema = build_artifact_validator.load_schema("acme.build.custom.v1")
            self.assertEqual(schema["schema_id"], "acme.build.custom.v1")

    def test_extra_root_cannot_shadow_base_schema_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write_schema(root, "requirements.v1.yaml", "gc.build.requirements.v1")
            with mock.patch.dict(os.environ, {"GC_BUILD_SCHEMA_ROOTS": str(root)}):
                schema = build_artifact_validator.load_schema("gc.build.requirements.v1")
            # Base-first ordering: the published base definition wins; the
            # shadow attempt in the extra root is never consulted.
            self.assertIn("workflow.id", schema.get("required_front_matter", []))

    def test_unset_env_is_byte_identical_base_behavior(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "GC_BUILD_SCHEMA_ROOTS"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                build_artifact_validator.schema_roots(),
                [build_artifact_validator.SCHEMA_ROOT],
            )
            with self.assertRaises(build_artifact_validator.ValidationError):
                build_artifact_validator.load_schema("acme.build.custom.v1")

    def test_missing_or_blank_extra_roots_are_skipped(self) -> None:
        bogus = os.pathsep.join(["", "  ", "/nonexistent/schema/root"])
        with mock.patch.dict(os.environ, {"GC_BUILD_SCHEMA_ROOTS": bogus}):
            self.assertEqual(
                build_artifact_validator.schema_roots(),
                [build_artifact_validator.SCHEMA_ROOT],
            )



class MaterializedBuildArtifactValidatorTests(unittest.TestCase):
    """A copy at <scope-root>/.gc/scripts/ resolves base schemas from the pack gc installed (gc-qcs76)."""

    PACK_ROOT = pathlib.Path(__file__).resolve().parents[1]
    VALIDATOR = PACK_ROOT / "assets" / "scripts" / "validate_build_artifact.py"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.scope_root = self.tmp / "root"
        scripts = self.scope_root / ".gc" / "scripts"
        scripts.mkdir(parents=True)
        self.copy = scripts / self.VALIDATOR.name
        self.copy.write_text(self.VALIDATOR.read_text(encoding="utf-8"), encoding="utf-8")
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self.calls = self.tmp / "gc-calls.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _fake_gc(self, search_paths: list[pathlib.Path]) -> None:
        payload = self.tmp / "formula-list.json"
        payload.write_text(json.dumps({"ok": True, "search_paths": [str(p) for p in search_paths]}), encoding="utf-8")
        gc = self.bin_dir / "gc"
        gc.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"$PWD $*\" >> '{self.calls}'\n"
            "[ \"$1 $2 $3\" = 'formula list --json' ] || exit 2\n"
            f"cat '{payload}'\n",
            encoding="utf-8",
        )
        gc.chmod(0o755)

    def _load_copy(self):
        name = f"materialized_validator_{id(self)}"
        spec = importlib.util.spec_from_file_location(name, self.copy)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None)
        spec.loader.exec_module(module)
        return module

    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k != "GC_BUILD_SCHEMA_ROOTS"}
        # Only the fake gc plus the system dirs bash/cat live in; never a real gc.
        env["PATH"] = os.pathsep.join([str(self.bin_dir), "/usr/bin", "/bin"])
        return env

    def _fake_pack(self, name: str, pack_name: str) -> pathlib.Path:
        pack = self.tmp / name
        (pack / "formulas").mkdir(parents=True)
        (pack / "pack.toml").write_text(f'[pack]\nname = "{pack_name}"\nschema = 2\n', encoding="utf-8")
        schemas = pack / "schemas" / "build"
        schemas.mkdir(parents=True)
        for schema in (self.PACK_ROOT / "schemas" / "build").glob("*.yaml"):
            (schemas / schema.name).write_text(schema.read_text(encoding="utf-8"), encoding="utf-8")
        return pack

    def test_copy_uses_installed_gascity_pack_as_base_root(self) -> None:
        other = self._fake_pack("other-pack", "compound-engineering")
        installed = self._fake_pack("installed-gascity", "gascity")
        self._fake_gc([other / "formulas", installed / "formulas", self.tmp / "missing" / "formulas"])
        validator = self._load_copy()
        with mock.patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(validator.schema_roots(), [installed / "schemas" / "build"])
            schema = validator.load_schema("gc.build.review.v1")
        self.assertEqual(schema["schema_id"], "gc.build.review.v1")
        # gc is asked from the scope root, so rig-level imports are honored.
        self.assertIn(str(self.scope_root.resolve()), self.calls.read_text(encoding="utf-8"))

    def test_copy_still_rejects_unknown_schema_ids(self) -> None:
        installed = self._fake_pack("installed-gascity", "gascity")
        self._fake_gc([installed / "formulas"])
        validator = self._load_copy()
        with mock.patch.dict(os.environ, self._env(), clear=True):
            with self.assertRaisesRegex(validator.ValidationError, "unknown build artifact schema 'acme.build.custom.v1'"):
                validator.load_schema("acme.build.custom.v1")

    def test_extra_root_cannot_shadow_discovered_base_schema(self) -> None:
        installed = self._fake_pack("installed-gascity", "gascity")
        self._fake_gc([installed / "formulas"])
        extra = self.tmp / "extra"
        extra.mkdir()
        (extra / "review.v1.yaml").write_text(
            "schema_id: gc.build.review.v1\n"
            "required_front_matter: [schema]\n"
            "allowed_statuses: [approved]\n"
            "coverage_statuses: [covered]\n"
            "required_sections: []\n",
            encoding="utf-8",
        )
        validator = self._load_copy()
        env = {**self._env(), "GC_BUILD_SCHEMA_ROOTS": str(extra)}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(validator.schema_roots(), [installed / "schemas" / "build", extra])
            schema = validator.load_schema("gc.build.review.v1")
        self.assertIn("workflow.id", schema["required_front_matter"])

    def test_copy_ignores_non_gascity_packs_and_fails_closed(self) -> None:
        other = self._fake_pack("other-pack", "compound-engineering")
        self._fake_gc([other / "formulas"])
        validator = self._load_copy()
        with mock.patch.dict(os.environ, self._env(), clear=True):
            with self.assertRaisesRegex(validator.ValidationError, "unknown build artifact schema 'gc.build.review.v1'"):
                validator.load_schema("gc.build.review.v1")

    def test_copy_without_gc_on_path_fails_closed(self) -> None:
        validator = self._load_copy()
        with mock.patch.dict(os.environ, self._env(), clear=True):
            with self.assertRaisesRegex(validator.ValidationError, "unknown build artifact schema 'gc.build.review.v1'"):
                validator.load_schema("gc.build.review.v1")

    def test_pack_tree_validator_never_asks_gc(self) -> None:
        self._fake_gc([])
        with mock.patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(build_artifact_validator.schema_roots(), [build_artifact_validator.SCHEMA_ROOT])
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
