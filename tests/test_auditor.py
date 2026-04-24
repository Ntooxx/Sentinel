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
        self.assertEqual(audit["risk_summary"]["maintainability"]["level"], "medium")
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


if __name__ == "__main__":
    unittest.main()
