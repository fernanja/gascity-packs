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
        self.assertIn("status: blocked", message)

    def test_permit_match_ignores_wrapping_emphasis_and_typographic_marks(self) -> None:
        # The requirements wrap the sentence, emphasise a word and use a curly
        # apostrophe, an em dash and blockquote markers; the quote has none.
        for permit in (
            "The mayor checks this post-merge;   it is not a\n worker AC.",
            "Repairing production rows is Jon's decision - the database is never edited directly.",
            "the MAYOR checks this Post-Merge; it is not a worker ac.",
            "...the mayor checks this post-merge; it is not a worker AC…",
        ):
            with self.subTest(permit=permit):
                self.validate(self.artifact(ac3_status="deferred", permit=permit))

    def test_permit_match_folds_links_tables_list_markers_and_ellipses(self) -> None:
        requirements = (
            "# Requirements\n\n"
            "- **AC-1:** one.\n- **AC-2:** two.\n- **AC-3:** three.\n\n"
            "## Hand-offs\n\n"
            "1. [x] The [bundle republish](https://example.test/contract#14) is _mayor-owned_ \u2026 workers have no access.\n"
            "* Screenshots are **Out of Scope** for this work.\n\n"
            "| Item | Disposition |\n| --- | --- |\n| 143 | `MetricTile` sizing is deferred to the next round \\| see OQ-5 |\n"
        )
        for permit in (
            "The bundle republish is mayor-owned ... workers have no access.",
            "- [ ] the bundle republish is mayor-owned… workers have no access",
            "Screenshots are out of scope for this work.",
            "143 MetricTile sizing is deferred to the next round",
            "| 143 | `MetricTile` sizing is deferred to the next round",
            "workers have no access. Screenshots are **Out of Scope** for this work.",
            # Two list items quoted on one line, with the markers kept inline.
            "- The bundle republish is mayor-owned ... workers have no access. - Screenshots are out of scope for this work.",
            "The bundle republish is mayor-owned \u2014 \u2026 workers have no access.",
        ):
            with self.subTest(permit=permit):
                self.validate(self.artifact(ac3_status="deferred", permit=permit), requirements=requirements)
        # Folding is not fuzzy matching: other words are still other words.
        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "permit text is not in the requirements artifact"):
            self.validate(
                self.artifact(ac3_status="deferred", permit="The bundle republish is out of scope for workers."),
                requirements=requirements,
            )

    def test_a_requirement_s_own_statement_is_not_a_permit(self) -> None:
        # gc-gdyaz review: "the text is in the requirements" let any requirement
        # permit its own deferral. The quote must hand the requirement off.
        for permit in (
            "`make preflight-fast` exits 0 and the pull request's checks are green.",
            "the new guard rejects a team-less item.",
            "after merge, the Dependabot alerts for these packages auto-close."[len("after merge, ") :],
        ):
            with self.subTest(permit=permit):
                with self.assertRaises(build_artifact_validator.ValidationError) as caught:
                    self.validate(self.artifact(ac3_status="deferred", permit=permit))
                message = str(caught.exception)
                self.assertIn("trace.coverage[AC-3]", message)
                self.assertIn("does not hand the requirement off", message)
                self.assertIn("A requirement's own statement is not a permit", message)
                self.assertIn(permit[:40], message)
                self.assertIn('"out of scope"', message)

    def test_hand_off_words_the_corpus_uses_are_accepted(self) -> None:
        hand_offs = (
            "AC-3 (post-merge, mayor): the mayor triggers the deploy and records the result.",
            "After merge, the mayor runs contract section 14 and records the new bundle hash.",
            "Out of scope: the first-import path, which is a separate bead.",
            "Not in scope: Accounts (next round), the Dashboard tab.",
            "Not in this work: the journal-entry editor.",
            "If it cannot be done reliably, say why in the summary and file a follow-up bead.",
            "The reply and the handoff log line are mayor-owned.",
            "Publishing to the Design project is mayor-side.",
            "Section 5: Needs Jon (product decision / data repair): flag, do not execute.",
            "This is an open user decision; don't touch it.",
            "Do not touch anything already covered by workflow gcas-imx9ei.",
            "Do not write to the Claude Design project directly.",
            "If OQ-5 is still open, AC-8b is blocked-by-DS (OQ-5) and the file is untouched.",
            "Each item has a disposition: implemented, different shape, or deferred (reason).",
            "Optional (ask the mayor first; this is CI config): decide whether these specs run in CI.",
            "The other three proposals are held separately.",
            "The decision replaces REQ-2's STOP condition.",
        )
        own_statements = (
            "`npm audit --omit=optional` shows no advisories for these three packages.",
            "Do not block unrelated evidence: decide whether Vitest still runs when ubs fails.",
            "Append a dated follow-up row instead of editing a row in place.",
            "Source handoff: Claude Design round 7 (relayed by the mayor, 2026-09-12).",
            "Verified by the mayor against origin/main on 2026-09-30.",
            "No change to the content of existing rows.",
            "The publish step marks the pull request ready only once review approves.",
            "It accepts an optional, precomputed hierarchy context.",
            "The chevron it held is decorative and promises navigation that does not exist.",
            "The full run on main ends with zero failures and zero skipped tests.",
        )
        for text in hand_offs:
            with self.subTest(hand_off=text):
                self.assertTrue(
                    build_artifact_validator.permit_cue(build_artifact_validator.normalize_permit_text(text)), text
                )
        for text in own_statements:
            with self.subTest(own_statement=text):
                self.assertEqual(
                    build_artifact_validator.permit_cue(build_artifact_validator.normalize_permit_text(text)), "", text
                )

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
        text = self.artifact(ac3_status="deferred", permit=self.PERMIT_AC3)
        with self.assertRaises(build_artifact_validator.ValidationError) as caught:
            build_artifact_validator.validate_artifact_text(
                text,
                expected_schema="gc.build.plan.v1",
                require_coverage_permits=True,
                requirements_sources=[],
                requirements_hint="record it with `gc bd update root --set-metadata gc.build.requirements_path=<path>`",
            )
        message = str(caught.exception)
        self.assertIn("1 coverage entries are not 'covered' (AC-3)", message)
        self.assertIn("the requirements path could not be resolved", message)
        self.assertIn("gc bd update root --set-metadata gc.build.requirements_path=<path>", message)
        # Nothing is left open, so nothing needs the requirements text.
        build_artifact_validator.validate_artifact_text(
            self.artifact(ac3_status="covered"),
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

    def omitting_ac3(self, schema: str = "gc.build.implementation-summary.v1", status: str = "approved") -> str:
        """An artifact that leaves AC-3 out of its own id list AND its coverage
        (plans/deploy-pipeline-fix-gcas-enknty/task-gcas-42nz6l-summary.md)."""
        return (
            self.artifact(schema, ac3_status="covered", status=status)
            .replace("ids: [AC-1, AC-2, AC-3]", "ids: [AC-1, AC-2]")
            .replace("    - id: AC-3\n      status: covered\n", "")
            .replace("| AC-3 | covered |", "")
        )

    def test_requirement_ids_come_from_the_requirements_not_from_the_artifact(self) -> None:
        for schema in self.SECTIONS:
            with self.subTest(schema=schema):
                text = self.omitting_ac3(schema)
                # The artifact is consistent with itself, so the old rule passes it.
                build_artifact_validator.validate_artifact_text(text, expected_schema=schema)
                with self.assertRaises(build_artifact_validator.ValidationError) as caught:
                    self.validate(text, schema)
                message = str(caught.exception)
                self.assertIn("every requirement id the requirements artifact defines", message)
                self.assertIn("missing from requirements.md: AC-3", message)
                self.assertNotIn("AC-1", message)

    def test_missing_ids_and_missing_permits_are_reported_together(self) -> None:
        requirements = self.REQUIREMENTS + "\n- **AC-4:** the summary lists the unshipped commits.\n"
        with self.assertRaises(build_artifact_validator.ValidationError) as caught:
            self.validate(self.artifact(ac3_status="deferred"), requirements=requirements)
        message = str(caught.exception)
        self.assertIn("missing from requirements.md: AC-4", message)
        self.assertIn("trace.coverage[AC-3] (status 'deferred'): missing permit", message)

    def test_an_artifact_that_is_not_approved_need_not_list_every_requirement(self) -> None:
        self.validate(self.omitting_ac3("gc.build.plan.v1", status="blocked"), "gc.build.plan.v1")
        self.validate(self.omitting_ac3(status="blocked"), "gc.build.implementation-summary.v1")

    def test_a_coverage_id_with_a_descriptive_suffix_accounts_for_its_requirement(self) -> None:
        text = (
            self.artifact(ac3_status="covered")
            .replace("ids: [AC-1, AC-2, AC-3]", "ids: [AC-1, AC-2, AC-3-alerts-auto-close]")
            .replace("    - id: AC-3\n", "    - id: AC-3-alerts-auto-close\n")
            .replace("| AC-3 | covered |", "| AC-3-alerts-auto-close | covered |")
        )
        self.validate(text)
        # AC-30 is another requirement, not AC-3 with a suffix.
        other = text.replace("AC-3-alerts-auto-close", "AC-30")
        with self.assertRaisesRegex(build_artifact_validator.ValidationError, "missing from requirements.md: AC-3"):
            self.validate(other)

    def test_requirement_ids_are_extracted_where_the_corpus_defines_them(self) -> None:
        text = (
            "---\n"
            "trace:\n  coverage:\n    - id: REQ-900\n      status: covered\n"
            "---\n"
            "# R10 \u2014 Settings tie-out (gcas-ttl6ov), CVE-2026-84377\n\n"
            "- **AC-1.** The cause, stated with a reproduction.\n"
            "- **AC-2 (R1):** every shard uploads a blob. See AC-1 and SCOPE-9 above.\n"
            "- **AC-3:** a normal run shows none.\n"
            "- AC-4: root-cause the interception.\n"
            "- AC-5. Red first.\n"
            "- AC-6 (R1): run the wrapper.\n"
            "- [ ] **AC-7 (REQ-001)** \u2014 an automated a11y scan finds zero live regions.\n"
            "- **AC-8b is now required.** Every tile value fits.\n"
            "- **SCOPE-1. Find the cause before changing anything.** Reproduce the failure.\n"
            "- **REQ-5 (new):** find the code path.\n"
            "- **TS-1** \u2014 refactor the badge.\n"
            "- **US-1** \u2014 as a screen-reader user I want quiet chrome.\n"
            "5. **OQ-5, tile fit (from Part 1 F-143-fit). This blocks item 12.**\n\n"
            "**AC-9 (item 1).**\n\n"
            "REQ-001: a new city-local order.\n"
            "OOS-1. No writes to the Claude Design project.\n"
            "OOS-2. No generic lint rule.\n"
            "CON-1: make commands only.\n\n"
            "### REQ-1 \u2014 the sync excludes teamless items\n\n"
            "## AC-2 AMENDMENT (mayor, 2026-09-23)\n\n"
            "> - **AC-10:** quoted criteria count too.\n\n"
            # None of these define a requirement id.
            "The original AC-99 (three clean runs) is replaced; cite AC-98' as the basis.\n"
            "AC-97 tolerance would fail on main without any code change.\n"
            "OQ-96;\n"
            "PYSEC-2026-3785, PYSEC-2026-3786 and GHSA-1234 are the advisories.\n"
            "- AC-95-full-suite-green: a slug, not an id.\n"
            "- AC-94 is met by the branch's four-instance run.\n"
            "- R1: fix the blob path. **R2:** assert the effect. ### R3: a label without a hyphen\n"
            "- **G1\u2013G4 are the whole distance.** **Q1** roster rhythm. **C3/C4** no retry.\n"
            "| REQ-93 | covered |\n"
            "```\n- **AC-92:** inside a code fence\n```\n"
        )
        self.assertEqual(
            build_artifact_validator.extract_requirement_ids(text),
            [
                "AC-1", "AC-2", "AC-3", "AC-4", "AC-5", "AC-6", "AC-7", "AC-8b", "SCOPE-1", "REQ-5", "TS-1", "US-1",
                "OQ-5", "AC-9", "REQ-001", "OOS-1", "OOS-2", "CON-1", "REQ-1", "AC-10",
            ],
        )

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
