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


class ArchetypeRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name)
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

    def test_raw_html_readme_name_fallback_to_folder(self):
        (self.project_root / "README.md").write_text(
            '<div align="center">\n<p align="center">\n<img src="logo.png">\n</p>\n</div>\n',
            encoding="utf-8",
        )
        (self.project_root / "src").mkdir(parents=True)
        (self.project_root / "src" / "main.py").write_text(
            "def main():\n    pass\n",
            encoding="utf-8",
        )

        audit, files = self._scan([".py", ".md"])
        project_name = audit["understanding"]["project_name"]
        self.assertNotIn("<", project_name)
        self.assertNotIn("div", project_name.lower())
        self.assertEqual(project_name, self.project_root.name)

    def test_generic_purpose_is_blocked(self):
        (self.project_root / "README.md").write_text(
            "# TensorFlow\n\napplication logic, application logic, application logic\n",
            encoding="utf-8",
        )
        (self.project_root / "src").mkdir(parents=True)
        (self.project_root / "src" / "core.py").write_text("def core(): pass\n", encoding="utf-8")

        audit, files = self._scan([".py", ".md"])
        purpose = audit["understanding"]["purpose"]
        self.assertNotEqual(purpose, "It is organized around application logic, application logic, and application logic.")
        self.assertNotIn("application logic, application logic", purpose)

    def test_framework_library_not_forced_into_runtime_tracing(self):
        (self.project_root / "tensorflow" / "python" / "keras").mkdir(parents=True)
        (self.project_root / "tensorflow" / "python" / "keras" / "layers.py").write_text(
            "class Layer:\n    pass\n",
            encoding="utf-8",
        )
        (self.project_root / "tensorflow" / "core").mkdir(parents=True)
        (self.project_root / "tensorflow" / "core" / "ops.cc").write_text(
            "void Op() {}\n",
            encoding="utf-8",
        )
        (self.project_root / "tensorflow" / "go" / "genop").mkdir(parents=True)
        (self.project_root / "tensorflow" / "go" / "genop" / "main.go").write_text(
            "package main\nfunc main() {}\n",
            encoding="utf-8",
        )
        (self.project_root / "third_party" / "xla" / "xla").mkdir(parents=True)
        (self.project_root / "third_party" / "xla" / "xla" / "service.cc").write_text(
            "void Service() {}\n",
            encoding="utf-8",
        )
        (self.project_root / "README.md").write_text(
            "# TensorFlow\n\nAn open source machine learning framework.\n",
            encoding="utf-8",
        )

        audit, files = self._scan([".py", ".cc", ".go", ".md"])
        archetype = audit["understanding"]["archetype"]
        self.assertEqual(archetype, "framework_library")

        workflow = audit["understanding"]["workflow_hints"]
        self.assertTrue(any("Framework/library repo detected" in h for h in workflow))

        # runtime risks should not include tests/examples/generators/lockfiles
        runtime_risks = audit["risk_groups"].get("runtime", [])
        for risk in runtime_risks:
            path = risk["file"].lower()
            self.assertNotIn("_test.", path)
            self.assertNotIn("/test/", path)
            self.assertNotIn("/tests/", path)
            self.assertNotIn("/examples/", path)
            self.assertNotIn("/gen/", path)
            self.assertNotIn("/generated/", path)

    def test_vendor_heavy_archetype(self):
        for i in range(30):
            (self.project_root / "third_party" / f"vendor{i}").mkdir(parents=True)
            (self.project_root / "third_party" / f"vendor{i}" / "lib.c").write_text(
                f"void vendor{i}() {{}}\n",
                encoding="utf-8",
            )
        (self.project_root / "src").mkdir(parents=True)
        (self.project_root / "src" / "main.c").write_text("int main() { return 0; }\n", encoding="utf-8")
        (self.project_root / "README.md").write_text("# Project\n", encoding="utf-8")

        audit, files = self._scan([".c", ".md"])
        secondary = audit["understanding"].get("archetype_secondary", [])
        self.assertIn("vendor_heavy", secondary)

    def test_dependency_lockfile_warning(self):
        lock = "\n".join(f"package=={i}.0.0" for i in range(1000))
        (self.project_root / "requirements_lock_3_10.txt").write_text(lock, encoding="utf-8")
        (self.project_root / "src").mkdir(parents=True)
        (self.project_root / "src" / "app.py").write_text("def main(): pass\n", encoding="utf-8")
        (self.project_root / "README.md").write_text("# App\n", encoding="utf-8")

        audit, files = self._scan([".py", ".txt", ".md"])
        lock_issues = [issue for issue in audit["issues"] if "requirements_lock" in str(issue.get("file", ""))]
        for issue in lock_issues:
            self.assertNotIn("module boundaries", issue["message"])
            self.assertIn("dependency", issue["message"].lower())

        # Lockfile should not appear in runtime risks
        runtime_risks = audit["risk_groups"].get("runtime", [])
        for risk in runtime_risks:
            self.assertNotIn("requirements_lock", risk["file"])

    def test_risk_factors_are_deduplicated(self):
        (self.project_root / "src").mkdir(parents=True)
        (self.project_root / "src" / "big.py").write_text(
            "import os\nimport sys\nimport json\n"
            + "\n".join(f"def func{i}(): pass" for i in range(300))
            + "\n",
            encoding="utf-8",
        )
        (self.project_root / "README.md").write_text("# App\n", encoding="utf-8")

        audit, files = self._scan([".py", ".md"])
        for risk in audit["risk_scores"]:
            factors = risk.get("factors", [])
            seen = set()
            for f in factors:
                self.assertNotIn(f.lower(), seen, f"Duplicate factor '{f}' in {risk['file']}")
                seen.add(f.lower())

    def test_monorepo_splits_components(self):
        (self.project_root / "packages" / "app" / "src").mkdir(parents=True)
        (self.project_root / "packages" / "app" / "src" / "main.ts").write_text(
            "console.log('app');\n",
            encoding="utf-8",
        )
        (self.project_root / "packages" / "sdk" / "src").mkdir(parents=True)
        (self.project_root / "packages" / "sdk" / "src" / "client.ts").write_text(
            "export class Client {}\n",
            encoding="utf-8",
        )
        (self.project_root / "packages" / "server" / "src").mkdir(parents=True)
        (self.project_root / "packages" / "server" / "src" / "index.ts").write_text(
            "import http from 'http';\n",
            encoding="utf-8",
        )
        (self.project_root / "README.md").write_text("# Monorepo\n", encoding="utf-8")

        audit, files = self._scan([".ts", ".md"])
        components = {c["path"]: c["role"] for c in audit["understanding"]["main_components"]}
        self.assertIn("packages/app", components)
        self.assertIn("packages/sdk", components)
        self.assertIn("packages/server", components)

    def test_browser_engine_archetype(self):
        (self.project_root / "Libraries" / "LibWeb").mkdir(parents=True)
        (self.project_root / "Libraries" / "LibWeb" / "Page.cpp").write_text(
            "void loadPage() {}\n",
            encoding="utf-8",
        )
        (self.project_root / "Libraries" / "LibJS").mkdir(parents=True)
        (self.project_root / "Libraries" / "LibJS" / "Parser.cpp").write_text(
            "void parse() {}\n",
            encoding="utf-8",
        )
        (self.project_root / "AK").mkdir(parents=True)
        (self.project_root / "AK" / "String.cpp").write_text(
            "class String {};\n",
            encoding="utf-8",
        )
        (self.project_root / "README.md").write_text("# Ladybird\n", encoding="utf-8")

        audit, files = self._scan([".cpp", ".md"])
        self.assertEqual(audit["understanding"]["archetype"], "browser_engine")
        self.assertIn("browser engine", audit["understanding"]["project_type"].lower())

    def test_desktop_app_archetype(self):
        (self.project_root / "src-tauri" / "src").mkdir(parents=True)
        (self.project_root / "src-tauri" / "src" / "main.rs").write_text(
            "fn main() { println!(\"hello\"); }\n",
            encoding="utf-8",
        )
        (self.project_root / "src").mkdir(parents=True)
        (self.project_root / "src" / "app.ts").write_text(
            "console.log('ui');\n",
            encoding="utf-8",
        )
        (self.project_root / "README.md").write_text("# Tolaria\n", encoding="utf-8")

        audit, files = self._scan([".rs", ".ts", ".md"])
        self.assertEqual(audit["understanding"]["archetype"], "desktop_app")

    def test_go_native_backend_archetype(self):
        (self.project_root / "cmd" / "server").mkdir(parents=True)
        (self.project_root / "cmd" / "server" / "main.go").write_text(
            "package main\nfunc main() {}\n",
            encoding="utf-8",
        )
        (self.project_root / "api").mkdir(parents=True)
        (self.project_root / "api" / "handler.go").write_text(
            "package api\nfunc Handle() {}\n",
            encoding="utf-8",
        )
        (self.project_root / "backend").mkdir(parents=True)
        (self.project_root / "backend" / "native.cpp").write_text(
            "void native() {}\n",
            encoding="utf-8",
        )
        (self.project_root / "go.mod").write_text(
            "module example.com/app\n\ngo 1.21\n",
            encoding="utf-8",
        )
        (self.project_root / "README.md").write_text("# App\n", encoding="utf-8")

        audit, files = self._scan([".go", ".cpp", ".md"])
        self.assertEqual(audit["understanding"]["primary_language"], "go")
        self.assertIn("backend", audit["understanding"]["project_type"].lower())

    def test_examples_are_not_primary_runtime_entry_points(self):
        (self.project_root / "examples" / "hello").mkdir(parents=True)
        (self.project_root / "examples" / "hello" / "main.go").write_text(
            "package main\nfunc main() {}\n",
            encoding="utf-8",
        )
        (self.project_root / "cmd" / "server").mkdir(parents=True)
        (self.project_root / "cmd" / "server" / "main.go").write_text(
            "package main\nfunc main() {}\n",
            encoding="utf-8",
        )
        (self.project_root / "README.md").write_text("# App\n", encoding="utf-8")

        audit, files = self._scan([".go", ".md"])
        runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
        example_entries = audit["structure"]["entry_points_by_category"].get("example", [])
        self.assertIn("cmd/server/main.go", runtime_entries)
        self.assertIn("examples/hello/main.go", example_entries)
        self.assertNotIn("examples/hello/main.go", runtime_entries)


if __name__ == "__main__":
    unittest.main()
