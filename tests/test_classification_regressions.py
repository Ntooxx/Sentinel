import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) in sys.path:
    sys.path.remove(str(SRC))
sys.path.insert(0, str(SRC))

from classify import classifyFile, riskFromScore, FileClassification  # noqa: E402
from auditor import ProjectAuditor  # noqa: E402


def _make_file(tmpdir: Path, rel_path: str, content: str = ""):
    full = tmpdir / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content or f"// {rel_path}\n", encoding="utf-8")


class ClassificationRegressionTests(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name)
        self.auditor = ProjectAuditor(
            str(self.project_root),
            str(self.project_root / "checkpoints.json"),
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _classify(self, path: str) -> FileClassification:
        return classifyFile(path)

    # --- Test 1: i18n files are localization/resource ---
    def test_i18n_classified_as_localization(self):
        fc = self._classify("packages/app/src/i18n/en.ts")
        self.assertTrue(fc.isLocalization)
        self.assertEqual(fc.role, "localization")

    def test_locales_classified_as_localization(self):
        fc = self._classify("packages/app/src/locales/de.ts")
        self.assertTrue(fc.isLocalization)

    def test_translations_classified_as_localization(self):
        fc = self._classify("packages/app/translations/ar.ts")
        self.assertTrue(fc.isLocalization)

    # --- Test 2: Generated SDK files are generated_sdk ---
    def test_gen_dot_ts_classified_as_generated_sdk(self):
        fc = self._classify("packages/sdk/js/src/v2/gen/types.gen.ts")
        self.assertTrue(fc.isGeneratedSdk)
        self.assertEqual(fc.role, "generated_sdk")
        self.assertIn("Generated SDK/client code", fc.manualEditPolicy)

    def test_generated_dir_classified_as_generated_sdk(self):
        fc = self._classify("packages/sdk/generated/client.ts")
        self.assertTrue(fc.isGeneratedSdk)

    def test_types_gen_ts_classified_as_generated_sdk(self):
        fc = self._classify("sdk.gen.ts")
        self.assertTrue(fc.isGeneratedSdk)

    def test_gen_go_classified_as_generated_sdk(self):
        fc = self._classify("api/gen/models.gen.go")
        self.assertTrue(fc.isGeneratedSdk)

    def test_generated_go_classified_as_generated_sdk(self):
        fc = self._classify("api/generated/client.go")
        self.assertTrue(fc.isGeneratedSdk)

    # --- Test 3: Markdown specs/docs are documentation/specification ---
    def test_markdown_spec_classified_as_specification(self):
        fc = self._classify("docs/specs/api-spec.md")
        self.assertTrue(fc.isDocumentation)
        self.assertIn("documentation/specification", fc.manualEditPolicy)

    def test_markdown_adr_classified_as_specification(self):
        fc = self._classify("docs/adr/0001-architecture.md")
        self.assertTrue(fc.isDocumentation)
        self.assertTrue(fc.isSpecification)

    def test_markdown_regular_doc_classified_as_documentation(self):
        fc = self._classify("docs/guide.md")
        self.assertTrue(fc.isDocumentation)
        self.assertFalse(fc.isRuntimeSource)

    def test_readme_md_is_documentation(self):
        fc = self._classify("README.md")
        self.assertTrue(fc.isDocumentation)
        self.assertFalse(fc.isRuntimeSource)

    # --- Test 4: Vendor files ---
    def test_vendor_classified_as_vendor(self):
        fc = self._classify("vendor/lib/some.js")
        self.assertTrue(fc.isVendor)

    def test_third_party_classified_as_vendor(self):
        fc = self._classify("third_party/foo/bar.py")
        self.assertTrue(fc.isVendor)

    def test_node_modules_classified_as_vendor(self):
        fc = self._classify("node_modules/express/index.js")
        self.assertTrue(fc.isVendor)

    # --- Test 5: RiskFromScore consistency ---
    def test_risk_from_score_high(self):
        self.assertEqual(riskFromScore(50), "high")
        self.assertEqual(riskFromScore(0), "high")
        self.assertEqual(riskFromScore(64), "high")

    def test_risk_from_score_medium(self):
        self.assertEqual(riskFromScore(65), "medium")
        self.assertEqual(riskFromScore(75), "medium")
        self.assertEqual(riskFromScore(84), "medium")

    def test_risk_from_score_low(self):
        self.assertEqual(riskFromScore(85), "low")
        self.assertEqual(riskFromScore(95), "low")
        self.assertEqual(riskFromScore(100), "low")

    # --- Test 6: Source files ---
    def test_source_file_is_source(self):
        fc = self._classify("src/main.ts")
        self.assertTrue(fc.isRuntimeSource)
        self.assertFalse(fc.isDocumentation)
        self.assertFalse(fc.isTest)
        self.assertFalse(fc.isLocalization)

    def test_main_rs_is_entry_candidate(self):
        fc = self._classify("src/main.rs")
        self.assertTrue(fc.isRuntimeEntryCandidate)

    def test_hotspot_file_is_hotspot_candidate(self):
        fc = self._classify("src/provider/models.ts")
        self.assertTrue(fc.isRuntimeHotspotCandidate)
        self.assertFalse(fc.isRuntimeEntryCandidate)

    # --- Test 7: Large file policy respects classification ---
    def test_large_i18n_policy_is_not_module_boundary(self):
        from classify import classifyLargeFilePolicy
        policy = classifyLargeFilePolicy("packages/app/src/i18n/en.ts")
        self.assertNotIn("module boundaries", policy)
        self.assertIn("localization", policy)

    def test_large_gen_sdk_policy_is_not_module_boundary(self):
        from classify import classifyLargeFilePolicy
        policy = classifyLargeFilePolicy("packages/sdk/gen/types.gen.ts")
        self.assertNotIn("module boundaries", policy)
        self.assertIn("regenerate", policy)

    def test_large_spec_policy_is_not_module_boundary(self):
        from classify import classifyLargeFilePolicy
        policy = classifyLargeFilePolicy("docs/specs/api-spec.md")
        self.assertNotIn("consider reviewing module boundaries", policy)
        self.assertIn("documentation/specification", policy)

    def test_large_source_policy_is_module_boundary(self):
        from classify import classifyLargeFilePolicy
        policy = classifyLargeFilePolicy("src/controller.ts")
        self.assertIn("module boundaries", policy)

    # --- Test 8: Monorepo component key splitting ---
    def test_monorepo_component_key_splits_packages(self):
        from classify import monorepo_component_key
        key = monorepo_component_key("packages/opencode/src/main.ts")
        self.assertEqual(key, "packages/opencode")

    def test_monorepo_component_key_splits_apps(self):
        from classify import monorepo_component_key
        key = monorepo_component_key("apps/web/src/index.ts")
        self.assertEqual(key, "apps/web")

    def test_monorepo_component_key_splits_services(self):
        from classify import monorepo_component_key
        key = monorepo_component_key("services/api/src/handler.ts")
        self.assertEqual(key, "services/api")

    def test_flat_component_key_is_root(self):
        from classify import monorepo_component_key
        key = monorepo_component_key("src/main.ts")
        self.assertEqual(key, "src")

    # --- Test 9: Entry point detection ---
    def test_is_runtime_entry_candidate_main_go(self):
        from classify import is_runtime_entry_candidate_by_name
        self.assertTrue(is_runtime_entry_candidate_by_name("cmd/server/main.go"))

    def test_is_runtime_entry_candidate_main_rs(self):
        from classify import is_runtime_entry_candidate_by_name
        self.assertTrue(is_runtime_entry_candidate_by_name("src/main.rs"))

    def test_is_not_runtime_entry_for_provider(self):
        from classify import is_runtime_entry_candidate_by_name
        self.assertFalse(is_runtime_entry_candidate_by_name("src/provider/models.ts"))

    def test_is_not_runtime_entry_for_test(self):
        from classify import is_runtime_entry_candidate_by_name
        self.assertFalse(is_runtime_entry_candidate_by_name("tests/test_main.py"))


class IntegratedClassificationTests(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def _scan_and_audit(self, extensions=None):
        auditor = ProjectAuditor(
            str(self.project_root),
            str(self.project_root / "checkpoints.json"),
        )
        files = auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=extensions or [".py", ".ts", ".md", ".json", ".yaml", ".rs", ".go", ".cpp", ".h"],
            max_size=1024 * 1024,
        )
        return auditor.audit_project(files), files

    def test_yolo_monorepo_subcomponents(self):
        layout = [
            ("packages/opencode/src/main.ts", "export function main() {}"),
            ("packages/opencode/src/provider/models.ts", "export class Models {}"),
            ("packages/app/src/index.ts", "export function start() {}"),
            ("packages/desktop/src-tauri/src/main.rs", "fn main() {}"),
            ("packages/desktop/src/main.ts", "export function desktop() {}"),
            ("packages/console/src/index.ts", "export function console() {}"),
            ("packages/sdk/js/src/v2/gen/types.gen.ts", "export type User {}"),
            ("packages/containers/Dockerfile", ""),
            ("packages/i18n/en.ts", "export default {hello: 'world'}"),
            ("packages/docs/README.md", "# Docs"),
            ("README.md", "# YOLO"),
        ]
        for path, content in layout:
            _make_file(self.project_root, path, content)

        audit, _ = self._scan_and_audit()
        components = audit["understanding"]["main_components"]

        # Should split packages into subcomponents, not just "packages"
        paths = {c["path"] for c in components}
        self.assertIn("packages/opencode", paths)
        self.assertIn("packages/app", paths)
        self.assertIn("packages/desktop", paths)

        # packages should not appear as a single "application logic" bucket
        for c in components:
            if c["path"] == "packages":
                self.fail(f"packages should be split into subcomponents, got role={c['role']}")

    def test_yolo_entry_points_correct(self):
        layout = [
            ("packages/opencode/src/index.ts", "export function main() {}"),
            ("packages/opencode/src/provider/models.ts", "export class Models {}"),
            ("packages/desktop/src-tauri/src/main.rs", "fn main() {}"),
        ]
        for path, content in layout:
            _make_file(self.project_root, path, content)
        _make_file(self.project_root, "README.md", "# YOLO")

        audit, _ = self._scan_and_audit()
        eps = audit["structure"]["entry_points_by_category"]

        # packages/opencode/src/index.ts may be a runtime entry point
        runtime = eps.get("runtime", [])
        # provider/models.ts should NOT be a runtime entry point
        self.assertNotIn("packages/opencode/src/provider/models.ts", runtime)
        # desktop main.rs SHOULD be a runtime entry point
        self.assertIn("packages/desktop/src-tauri/src/main.rs", runtime)

    def test_i18n_no_module_boundary_warning(self):
        _make_file(self.project_root, "packages/app/src/i18n/en.ts", "\n" * 600)
        _make_file(self.project_root, "README.md", "# Test")

        audit, _ = self._scan_and_audit()
        for issue in audit["issues"]:
            if issue["type"] in ("large_file", "large_file_size"):
                msg = issue.get("message", "")
                self.assertNotIn("module boundaries", msg,
                                 f"i18n file should not mention module boundaries: {msg}")

    def test_gen_sdk_no_module_boundary_warning(self):
        _make_file(self.project_root, "packages/sdk/gen/types.gen.ts", "\n" * 600)
        _make_file(self.project_root, "README.md", "# Test")

        audit, _ = self._scan_and_audit()
        for issue in audit["issues"]:
            if issue["type"] in ("large_file", "large_file_size"):
                msg = issue.get("message", "")
                self.assertNotIn("module boundaries", msg,
                                 f"gen sdk file should not mention module boundaries: {msg}")

    def test_markdown_spec_no_module_boundary_warning(self):
        _make_file(self.project_root, "docs/specs/api-spec.md", "\n" * 600)
        _make_file(self.project_root, "README.md", "# Test")

        audit, _ = self._scan_and_audit()
        for issue in audit["issues"]:
            if issue["type"] in ("large_file", "large_file_size"):
                msg = issue.get("message", "")
                self.assertNotIn("consider reviewing module boundaries", msg,
                                 f"spec md should not mention module boundaries: {msg}")

    def test_maintainability_risk_matches_score(self):
        _make_file(self.project_root, "src/main.py",
                   "import os\n\ndef main():\n    return os.name\n\nif __name__ == '__main__':\n    main()\n")
        _make_file(self.project_root, "README.md", "# Test")

        audit, _ = self._scan_and_audit()
        # Get maintainability from breakdown
        breakdown = audit.get("health_score_data", {}).get("breakdown", {})
        maintainability_pct = breakdown.get("maintainability_percent", 85)
        breakdown_risk = breakdown.get("maintainability_risk", "unknown")
        expected_risk = riskFromScore(maintainability_pct)
        self.assertEqual(
            breakdown_risk, expected_risk,
            f"Maintainability {maintainability_pct}% -> risk '{breakdown_risk}' should be '{expected_risk}'"
        )

        # Risk summary should also match
        risk_summary = audit.get("risk_summary", {})
        summary_risk = risk_summary.get("maintainability", {}).get("level", "unknown")
        self.assertEqual(
            summary_risk, expected_risk,
            f"Risk summary maintainability '{summary_risk}' should be '{expected_risk}'"
        )

    def test_purpose_no_raw_html(self):
        _make_file(self.project_root, "README.md",
                   '<p align="center"><img src="logo.png"/></p>\n\n# My Project\n\nThis is a cool project.')
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")

        audit, _ = self._scan_and_audit()
        purpose = audit["understanding"].get("purpose", "")
        self.assertNotIn("<p", purpose)
        self.assertNotIn("<img", purpose)
        self.assertNotIn("align=", purpose)

    def test_scan_coverage_warning_is_specific(self):
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")
        _make_file(self.project_root, "go.mod", "module test\n")
        _make_file(self.project_root, "README.md", "# Test")

        audit, _ = self._scan_and_audit()
        warning = audit.get("scan_coverage", {}).get("warning", "")
        if warning:
            self.assertNotIn("appear underrepresented", warning,
                             "Coverage warning should name specific directories, not be vague")

    def test_test_signal_wording(self):
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")
        _make_file(self.project_root, "tests/test_main.py", "def test_main(): pass\n")
        _make_file(self.project_root, "README.md", "# Test")

        audit, _ = self._scan_and_audit()
        risk_summary = audit.get("risk_summary", {})
        signal = risk_summary.get("test", {}).get("level", "")
        reason = risk_summary.get("test", {}).get("reason", "")
        self.assertNotEqual(signal, "unknown",
                            "Test signal should not be 'unknown' when test files exist")
        if signal == "present" or signal == "strong":
            self.assertTrue(reason, "Test signal should have a reason")


if __name__ == "__main__":
    unittest.main()
