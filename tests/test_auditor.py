import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) in sys.path:
    sys.path.remove(str(SRC))
sys.path.insert(0, str(SRC))

from auditor import ProjectAuditor  # noqa: E402


class ProjectAuditorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name)
        (self.project_root / "src").mkdir()
        (self.project_root / "tests").mkdir()

        (self.project_root / "README.md").write_text("# Sample Project\n", encoding="utf-8")
        (self.project_root / "requirements.txt").write_text("requests\n", encoding="utf-8")
        (self.project_root / "src" / "app.py").write_text(
            "import os\n\n"
            "class App:\n"
            "    pass\n\n"
            "def main():\n"
            "    return os.name\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8",
        )
        (self.project_root / "tests" / "test_app.py").write_text(
            "def test_smoke():\n"
            "    assert True\n",
            encoding="utf-8",
        )

        self.auditor = ProjectAuditor(
            str(self.project_root),
            str(self.project_root / "checkpoints.json"),
            str(ROOT / "config" / "audit_rules.json"),
            str(ROOT / "config" / "patterns.json"),
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_scan_and_audit_detect_structure_and_patterns(self):
        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md", ".txt"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)

        self.assertIn("src/app.py", files)
        self.assertTrue(audit["structure"]["has_tests"])
        self.assertNotIn("no_tests", {issue["type"] for issue in audit["issues"]})

        pattern_names = {pattern["name"] for pattern in audit["patterns"]}
        self.assertIn("automated_tests", pattern_names)
        self.assertIn("command_line_interface", pattern_names)
        self.assertIn("documentation", pattern_names)
        self.assertEqual(audit["understanding"]["primary_language"], "python")
        self.assertIn("summary", audit["understanding"])
        self.assertTrue(audit["understanding"]["main_components"])

    def test_checkpoint_diff_detects_modified_files(self):
        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md", ".txt"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)
        first_diff = self.auditor.diff_from_last_checkpoint(files)

        self.assertTrue(first_diff["is_first_scan"])

        self.auditor.create_checkpoint(files, audit)
        app_path = self.project_root / "src" / "app.py"
        app_path.write_text(
            app_path.read_text(encoding="utf-8") + "\n# updated\n",
            encoding="utf-8",
        )

        updated_files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md", ".txt"],
            max_size=1024 * 1024,
        )
        diff = self.auditor.diff_from_last_checkpoint(updated_files)

        self.assertFalse(diff["is_first_scan"])
        self.assertEqual(diff["modified_count"], 1)
        self.assertIn("src/app.py", diff["modified_files"])

    def test_large_files_are_maintainability_penalty_not_health_failure(self):
        for index in range(13):
            (self.project_root / "src" / f"large_{index}.py").write_text(
                "\n".join(f"VALUE_{line} = {line}" for line in range(520)),
                encoding="utf-8",
            )

        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md", ".txt"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)

        issue_types = [issue["type"] for issue in audit["issues"]]
        self.assertEqual(issue_types.count("large_file"), 13)
        self.assertGreaterEqual(audit["health_score"], 70)
        self.assertLess(audit["health_score"], 90)
        # Maintainability risk in summary must match the health breakdown
        health_data = audit["health_score_data"]
        breakdown = health_data["breakdown"]
        self.assertEqual(
            audit["risk_summary"]["maintainability"]["level"],
            breakdown["maintainability_risk"],
        )
        self.assertEqual(audit["risk_summary"]["security"]["level"], "not_assessed")

    def test_large_data_files_are_low_maintainability_risk(self):
        (self.project_root / "nvidia_nim_models.json").write_text(
            "[\n" + ",\n".join('  {"id": "model-%s"}' % line for line in range(600)) + "\n]\n",
            encoding="utf-8",
        )

        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md", ".txt", ".json"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)

        data_issue = next(issue for issue in audit["issues"] if issue.get("file") == "nvidia_nim_models.json")
        self.assertEqual(data_issue["category"], "maintainability")
        self.assertIn("validate schema", data_issue["message"])

        data_risk = next(item for item in audit["risk_scores"] if item["file"] == "nvidia_nim_models.json")
        self.assertEqual(data_risk["level"], "low")
        self.assertIn("maintainability", data_risk["risk_categories"])
        self.assertLessEqual(data_risk["score"], 20)

    def test_related_test_files_are_reported_in_risk_coverage(self):
        (self.project_root / "server.py").write_text(
            "def serve():\n"
            "    return 'ok'\n\n"
            "if __name__ == '__main__':\n"
            "    serve()\n",
            encoding="utf-8",
        )
        (self.project_root / "tests" / "test_server_module.py").write_text(
            "def test_server_import():\n"
            "    assert True\n",
            encoding="utf-8",
        )

        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md", ".txt"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)

        server_risk = next(item for item in audit["risk_scores"] if item["file"] == "server.py")
        self.assertEqual(server_risk["coverage"]["status"], "related")
        self.assertEqual(server_risk["coverage"]["test_file"], "tests/test_server_module.py")
        self.assertNotIn("no obvious paired test", server_risk["factors"])

    def test_generated_files_are_classified_separately(self):
        # Simulate Tolaria's generated files under src-tauri/gen/
        (self.project_root / "src-tauri" / "gen" / "apple" / "assets" / "mcp-server").mkdir(parents=True)
        (self.project_root / "src-tauri" / "gen" / "apple" / "assets" / "mcp-server" / "index.js").write_text(
            "// generated\n" + "\n".join(f"const x{i} = {i};" for i in range(300)),
            encoding="utf-8",
        )
        (self.project_root / "src-tauri" / "src").mkdir(parents=True)
        (self.project_root / "src-tauri" / "src" / "main.rs").write_text(
            "fn main() {\n    println!(\"hello\");\n}\n",
            encoding="utf-8",
        )

        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md", ".txt", ".js", ".rs"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)

        # Generated file should be classified as generated_sdk (project-owned generated code)
        gen_file = "src-tauri/gen/apple/assets/mcp-server/index.js"
        self.assertIn(gen_file, files)
        self.assertEqual(self.auditor._classify_path_context(gen_file, files[gen_file]), "generated_sdk")

        # Runtime hotspots should NOT include generated files
        runtime_hotspots = audit["understanding"]["hotspot_groups"].get("runtime", [])
        runtime_paths = [h["path"] for h in runtime_hotspots]
        self.assertNotIn(gen_file, runtime_paths)

        # main.rs should be the runtime entry point
        runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
        self.assertIn("src-tauri/src/main.rs", runtime_entries)

    def test_maintainability_score_matches_risk_level(self):
        # Create a project with moderate issues to trigger medium maintainability
        for index in range(8):
            (self.project_root / "src" / f"large_{index}.py").write_text(
                "\n".join(f"VALUE_{line} = {line}" for line in range(520)),
                encoding="utf-8",
            )

        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md", ".txt"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)

        health_data = audit["health_score_data"]
        breakdown = health_data["breakdown"]
        maintainability_pct = breakdown["maintainability_percent"]
        maintainability_risk = breakdown["maintainability_risk"]

        # They must be consistent: high pct -> low risk, low pct -> high risk
        if maintainability_pct >= 85:
            self.assertEqual(maintainability_risk, "low")
        elif maintainability_pct >= 65:
            self.assertEqual(maintainability_risk, "medium")
        else:
            self.assertEqual(maintainability_risk, "high")

    def test_test_signal_present_when_tests_exist(self):
        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md", ".txt"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)

        test_level = audit["risk_summary"]["test"]["level"]
        self.assertNotEqual(test_level, "unknown")
        self.assertIn(test_level, {"present", "strong"})

    def test_e2e_and_demo_classified_correctly(self):
        (self.project_root / "e2e").mkdir()
        (self.project_root / "e2e" / "core.spec.ts").write_text("describe('app', () => { it('works', () => {}); });\n", encoding="utf-8")
        (self.project_root / "demo-vault-v2").mkdir()
        (self.project_root / "demo-vault-v2" / "data.json").write_text('{"sample": true}\n', encoding="utf-8")

        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md", ".txt", ".ts", ".json"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)

        components = {c["path"]: c["role"] for c in audit["understanding"]["main_components"]}
        self.assertEqual(components.get("e2e"), "end-to-end tests")
        self.assertEqual(components.get("demo-vault-v2"), "demo/sample data")

    def test_documentation_risk_shows_code_examples_not_executable(self):
        (self.project_root / "docs").mkdir()
        (self.project_root / "docs" / "GUIDE.md").write_text(
            "# Guide\n\n```python\nclass Example:\n    pass\n```\n" + "\n".join(f"Line {i}" for i in range(300)),
            encoding="utf-8",
        )

        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md", ".txt"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)

        doc_risk = next((r for r in audit["risk_scores"] if r["file"] == "docs/GUIDE.md"), None)
        if doc_risk:
            self.assertNotIn("executable code", doc_risk["factors"])
            self.assertIn("contains code examples", doc_risk["factors"])


class MonorepoClassificationTests(unittest.TestCase):
    """Regression tests for YOLO-style monorepo improvements."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name)
        (self.project_root / "README.md").write_text("# YOLO Monorepo\n", encoding="utf-8")
        self.auditor = ProjectAuditor(
            str(self.project_root),
            str(self.project_root / "checkpoints.json"),
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _scan(self, extensions):
        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=extensions,
            max_size=1024 * 1024,
        )
        return self.auditor.audit_project(files), files

    def test_monorepo_components_are_split_by_second_level(self):
        # Simulate packages/opencode, packages/app, packages/desktop, packages/sdk
        (self.project_root / "packages" / "opencode" / "src").mkdir(parents=True)
        (self.project_root / "packages" / "opencode" / "src" / "cli.py").write_text(
            "def main():\n    return 'opencode'\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8",
        )
        (self.project_root / "packages" / "app" / "src").mkdir(parents=True)
        (self.project_root / "packages" / "app" / "src" / "main.py").write_text(
            "def main():\n    return 'app'\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8",
        )
        (self.project_root / "packages" / "desktop" / "src").mkdir(parents=True)
        (self.project_root / "packages" / "desktop" / "src" / "main.rs").write_text(
            "fn main() {\n    println!(\"hello\");\n}\n",
            encoding="utf-8",
        )
        (self.project_root / "packages" / "sdk" / "js" / "src").mkdir(parents=True)
        (self.project_root / "packages" / "sdk" / "js" / "src" / "client.ts").write_text(
            "export class Client {}\n",
            encoding="utf-8",
        )

        audit, files = self._scan([".py", ".rs", ".ts"])

        # packages/ should be split into subcomponents
        components = {c["path"]: c["role"] for c in audit["understanding"]["main_components"]}
        self.assertIn("packages/opencode", components, "packages/opencode should be a separate component")
        self.assertIn("packages/app", components, "packages/app should be a separate component")
        self.assertIn("packages/desktop", components, "packages/desktop should be a separate component")
        # The old top-level packages key should NOT be present (only subcomponents)
        self.assertNotIn("packages", components, "top-level packages should not be a component when subpackages exist")

        # Check roles
        self.assertEqual(components.get("packages/opencode"), "CLI / AI coding agent core")
        self.assertEqual(components.get("packages/app"), "frontend application")
        self.assertEqual(components.get("packages/desktop"), "desktop shell")

    def test_i18n_files_are_classified_as_localization_resource(self):
        (self.project_root / "packages" / "app" / "src" / "i18n").mkdir(parents=True)
        (self.project_root / "packages" / "app" / "src" / "i18n" / "en.ts").write_text(
            "export const messages = {\n  greeting: 'Hello'\n};\n",
            encoding="utf-8",
        )
        (self.project_root / "packages" / "app" / "src" / "i18n" / "fr.ts").write_text(
            "export const messages = {\n  greeting: 'Bonjour'\n};\n",
            encoding="utf-8",
        )
        (self.project_root / "packages" / "app" / "locales").mkdir(parents=True)
        (self.project_root / "packages" / "app" / "locales" / "de.json").write_text(
            '{"greeting": "Hallo"}\n',
            encoding="utf-8",
        )

        audit, files = self._scan([".ts", ".json"])

        # The i18n files should be classified as localization_resource
        en_file = "packages/app/src/i18n/en.ts"
        self.assertIn(en_file, files)
        self.assertEqual(
            self.auditor._classify_path_context(en_file, files[en_file]),
            "localization_resource",
        )

        fr_file = "packages/app/src/i18n/fr.ts"
        self.assertIn(fr_file, files)
        self.assertEqual(
            self.auditor._classify_path_context(fr_file, files[fr_file]),
            "localization_resource",
        )

        de_file = "packages/app/locales/de.json"
        self.assertIn(de_file, files)
        self.assertEqual(
            self.auditor._classify_path_context(de_file, files[de_file]),
            "localization_resource",
        )

        # Should not generate "review module boundaries" warning for i18n files
        i18n_issues = [issue for issue in audit["issues"] if en_file in str(issue.get("file", ""))]
        for issue in i18n_issues:
            self.assertNotIn("module boundaries", issue["message"])
        fr_issues = [issue for issue in audit["issues"] if fr_file in str(issue.get("file", ""))]
        for issue in fr_issues:
            self.assertNotIn("module boundaries", issue["message"])

    def test_generated_sdk_files_are_classified_properly(self):
        (self.project_root / "packages" / "sdk" / "js" / "src" / "gen").mkdir(parents=True)
        (self.project_root / "packages" / "sdk" / "js" / "src" / "gen" / "sdk.gen.ts").write_text(
            "// Auto-generated SDK\n" + "\n".join("export const api" + str(i) + " = {};" for i in range(300)),
            encoding="utf-8",
        )
        (self.project_root / "packages" / "sdk" / "js" / "src" / "gen" / "types.gen.ts").write_text(
            "// Auto-generated types\n" + "\n".join("export interface Type" + str(i) + " {}" for i in range(200)),
            encoding="utf-8",
        )

        audit, files = self._scan([".ts"])

        # Generated SDK files should be classified as generated_sdk
        sdk_file = "packages/sdk/js/src/gen/sdk.gen.ts"
        self.assertIn(sdk_file, files)
        self.assertEqual(
            self.auditor._classify_path_context(sdk_file, files[sdk_file]),
            "generated_sdk",
        )

        types_file = "packages/sdk/js/src/gen/types.gen.ts"
        self.assertIn(types_file, files)
        self.assertEqual(
            self.auditor._classify_path_context(types_file, files[types_file]),
            "generated_sdk",
        )

        # Should use generated SDK wording, not "review module boundaries"
        sdk_issues = [issue for issue in audit["issues"] if sdk_file in str(issue.get("file", ""))]
        for issue in sdk_issues:
            self.assertIn("Generated SDK/client file", issue["message"])

        # Generated SDK files should not appear in runtime hotspot groups
        runtime_hotspots = audit["understanding"]["hotspot_groups"].get("runtime", [])
        runtime_paths = [h["path"] for h in runtime_hotspots]
        self.assertNotIn(sdk_file, runtime_paths)
        self.assertNotIn(types_file, runtime_paths)

    def test_specification_markdown_is_classified_appropriately(self):
        (self.project_root / "packages" / "app" / "docs").mkdir(parents=True)
        (self.project_root / "packages" / "app" / "docs" / "create-effect-simplification-spec.md").write_text(
            "# Effect Simplification\n\n## Design\nReduce complexity.\n" + "\n".join(f"Section {i}" for i in range(200)),
            encoding="utf-8",
        )
        (self.project_root / "specs").mkdir()
        (self.project_root / "specs" / "api-design.md").write_text(
            "# API Design\n\n" + "\n".join(f"Endpoint {i}" for i in range(150)),
            encoding="utf-8",
        )

        audit, files = self._scan([".md"])

        # Spec docs should be classified as specification_documentation
        spec_file = "packages/app/docs/create-effect-simplification-spec.md"
        self.assertIn(spec_file, files)
        self.assertEqual(
            self.auditor._classify_path_context(spec_file, files[spec_file]),
            "specification_documentation",
        )

        specs_dir_file = "specs/api-design.md"
        self.assertIn(specs_dir_file, files)
        self.assertEqual(
            self.auditor._classify_path_context(specs_dir_file, files[specs_dir_file]),
            "specification_documentation",
        )

        # Should use spec/doc wording, not "module boundaries"
        spec_issues = [issue for issue in audit["issues"] if spec_file in str(issue.get("file", ""))]
        for issue in spec_issues:
            self.assertNotIn("module boundaries", issue["message"])
            self.assertIn("documentation/specification", issue["message"])

    def test_maintainability_risk_consistent_with_score(self):
        # Create a project with moderate issues
        for index in range(8):
            (self.project_root / "src").mkdir(parents=True, exist_ok=True)
            (self.project_root / "src" / f"large_{index}.py").write_text(
                "\n".join(f"VALUE_{line} = {line}" for line in range(520)),
                encoding="utf-8",
            )

        audit, files = self._scan([".py", ".md"])

        health_data = audit["health_score_data"]
        breakdown = health_data["breakdown"]
        maintainability_pct = breakdown["maintainability_percent"]
        maintainability_risk = breakdown["maintainability_risk"]
        risk_summary_maint = audit["risk_summary"]["maintainability"]["level"]

        # All three must be consistent
        if maintainability_pct >= 85:
            expected = "low"
        elif maintainability_pct >= 65:
            expected = "medium"
        else:
            expected = "high"
        self.assertEqual(maintainability_risk, expected)
        self.assertEqual(risk_summary_maint, expected)

    def test_runtime_entry_points_are_accurate(self):
        (self.project_root / "src").mkdir(parents=True)
        (self.project_root / "src" / "main.py").write_text(
            "def main():\n    return 'ok'\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8",
        )
        (self.project_root / "src" / "provider.py").write_text(
            "class Provider:\n    pass\n",
            encoding="utf-8",
        )
        (self.project_root / "src" / "models.py").write_text(
            "class Model:\n    pass\n",
            encoding="utf-8",
        )
        (self.project_root / "src" / "cache.py").write_text(
            "class Cache:\n    pass\n",
            encoding="utf-8",
        )

        audit, files = self._scan([".py", ".md"])

        # Entry points should only include main.py, not provider/models/cache
        entry_points = audit["structure"]["entry_point_details"]
        entry_paths = {ep["path"] for ep in entry_points}
        self.assertIn("src/main.py", entry_paths)
        self.assertNotIn("src/provider.py", entry_paths)
        self.assertNotIn("src/models.py", entry_paths)
        self.assertNotIn("src/cache.py", entry_paths)

    def test_test_signal_strong_when_tests_detected(self):
        (self.project_root / "tests").mkdir(parents=True, exist_ok=True)
        (self.project_root / "tests" / "test_core.py").write_text(
            "def test_core():\n    assert True\n",
            encoding="utf-8",
        )
        (self.project_root / "src").mkdir(parents=True, exist_ok=True)
        (self.project_root / "src" / "core.py").write_text(
            "def core():\n    return 1\n",
            encoding="utf-8",
        )

        audit, files = self._scan([".py", ".md"])

        test_level = audit["risk_summary"]["test"]["level"]
        self.assertIn(test_level, {"present", "strong"})
        self.assertNotEqual(test_level, "missing")

    def test_test_signal_missing_when_no_tests(self):
        (self.project_root / "src").mkdir(parents=True, exist_ok=True)
        (self.project_root / "src" / "core.py").write_text(
            "def core():\n    return 1\n",
            encoding="utf-8",
        )

        audit, files = self._scan([".py", ".md"])

        test_level = audit["risk_summary"]["test"]["level"]
        self.assertEqual(test_level, "missing")


if __name__ == "__main__":
    unittest.main()
