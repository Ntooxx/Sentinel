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


class LadybirdRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name)
        (self.project_root / "README.md").write_text("# Ladybird\nA browser engine.\n", encoding="utf-8")
        (self.project_root / "CMakeLists.txt").write_text("project(Ladybird)\n", encoding="utf-8")
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
        return self.auditor.audit_project(files)

    def test_ladybird_like_components_and_entry_points_are_classified(self):
        (self.project_root / "Libraries" / "LibMain").mkdir(parents=True)
        (self.project_root / "Libraries" / "LibMain" / "Main.cpp").write_text(
            "int main() {\n    return 0;\n}\n",
            encoding="utf-8",
        )
        (self.project_root / "Libraries" / "LibWeb" / "DOM").mkdir(parents=True)
        (self.project_root / "Libraries" / "LibWeb" / "DOM" / "Document.cpp").write_text(
            "class Document {};\n",
            encoding="utf-8",
        )
        (self.project_root / "Libraries" / "LibGfx").mkdir(parents=True)
        (self.project_root / "Libraries" / "LibGfx" / "Bitmap.cpp").write_text(
            "class Bitmap {};\n",
            encoding="utf-8",
        )
        (self.project_root / "Libraries" / "LibWasm").mkdir(parents=True)
        (self.project_root / "Libraries" / "LibWasm" / "Validator.cpp").write_text(
            "class Validator {};\n",
            encoding="utf-8",
        )
        (self.project_root / "Tests" / "LibWeb" / "Text" / "input" / "wpt-import" / "url" / "resources").mkdir(parents=True)
        (self.project_root / "Tests" / "LibWeb" / "Text" / "input" / "wpt-import" / "url" / "resources" / "urltestdata.json").write_text(
            "{\n  \"value\": 1\n}\n",
            encoding="utf-8",
        )
        (self.project_root / "Tests" / "LibJS" / "Runtime").mkdir(parents=True)
        (self.project_root / "Tests" / "LibJS" / "Runtime" / "smoke.js").write_text(
            "function test() { return true; }\n",
            encoding="utf-8",
        )
        (self.project_root / "Documentation").mkdir()
        (self.project_root / "Documentation" / "Guide.md").write_text("# Guide\n", encoding="utf-8")
        (self.project_root / "Meta" / "Generators").mkdir(parents=True)
        (self.project_root / "Meta" / "Generators" / "generate_ipc_definitions.py").write_text(
            "if __name__ == '__main__':\n    print('gen')\n",
            encoding="utf-8",
        )
        (self.project_root / ".devcontainer" / "features" / "ladybird").mkdir(parents=True)
        (self.project_root / ".devcontainer" / "features" / "ladybird" / "install.sh").write_text(
            "#!/usr/bin/env bash\necho setup\n",
            encoding="utf-8",
        )

        audit = self._scan([".cpp", ".py", ".sh", ".md", ".json", ".js", ".txt"])

        components = {item["path"]: item["role"] for item in audit["understanding"]["main_components"]}
        self.assertEqual(components["Libraries/LibWeb"], "browser engine code")
        self.assertEqual(components["Tests/LibWeb"], "test suite / WPT fixtures")
        self.assertEqual(components["Tests/LibJS"], "JavaScript engine tests")
        self.assertEqual(components["Libraries/LibGfx"], "graphics, fonts, and image codecs")
        self.assertEqual(components["Libraries/LibWasm"], "WebAssembly runtime and validation")
        self.assertEqual(components["Documentation"], "documentation")

        entry_points_by_category = audit["structure"]["entry_points_by_category"]
        self.assertEqual(entry_points_by_category["runtime"][0], "Libraries/LibMain/Main.cpp")
        self.assertIn("Meta/Generators/generate_ipc_definitions.py", entry_points_by_category["generator"])
        self.assertIn(".devcontainer/features/ladybird/install.sh", entry_points_by_category["environment"])
        self.assertTrue(audit["understanding"]["workflow_hints"][0].startswith("Start runtime tracing from Libraries/LibMain/Main.cpp"))

    def test_scan_coverage_warning_detects_underrepresented_source_directories(self):
        (self.project_root / "Libraries" / "LibWeb").mkdir(parents=True)
        for index in range(120):
            (self.project_root / "Libraries" / "LibWeb" / f"File{index}.cpp").write_text(
                "int value() {\n    return 1;\n}\n",
                encoding="utf-8",
            )
        (self.project_root / "Tests" / "LibWeb").mkdir(parents=True)
        for index in range(140):
            (self.project_root / "Tests" / "LibWeb" / f"test_{index}.js").write_text(
                "function run() {\n    return true;\n}\n",
                encoding="utf-8",
            )

        broken_audit = self._scan([".js", ".md", ".json"])
        self.assertTrue(broken_audit["scan_coverage"]["warning"])
        self.assertIn("Libraries/LibWeb", broken_audit["scan_coverage"]["underrepresented_directories"])

        fixed_audit = self._scan([".cpp", ".js", ".md", ".json"])
        self.assertFalse(fixed_audit["scan_coverage"]["warning"])
        self.assertEqual(fixed_audit["understanding"]["primary_language"], "c++")

    def test_primary_hotspots_favor_runtime_over_test_fixtures(self):
        (self.project_root / "Libraries" / "LibMain").mkdir(parents=True)
        (self.project_root / "Libraries" / "LibMain" / "Main.cpp").write_text(
            "int main() {\n    return 0;\n}\n",
            encoding="utf-8",
        )
        (self.project_root / "Libraries" / "LibWeb" / "Crypto").mkdir(parents=True)
        runtime_lines = "\n".join(f"int crypto_{i}() {{ return {i}; }}" for i in range(900))
        (self.project_root / "Libraries" / "LibWeb" / "Crypto" / "CryptoAlgorithms.cpp").write_text(
            runtime_lines + "\n// TODO: improve\n",
            encoding="utf-8",
        )
        (self.project_root / "Tests" / "LibWeb" / "Text" / "input" / "wpt-import" / "url" / "resources").mkdir(parents=True)
        fixture_lines = "\n".join(f'  "value_{i}": {i}' for i in range(1200))
        (self.project_root / "Tests" / "LibWeb" / "Text" / "input" / "wpt-import" / "url" / "resources" / "IdnaTestV2.json").write_text(
            "{\n" + fixture_lines + "\n}\n",
            encoding="utf-8",
        )

        audit = self._scan([".cpp", ".json", ".md"])

        primary_hotspots = audit["understanding"]["hotspots"]
        self.assertTrue(primary_hotspots)
        self.assertEqual(primary_hotspots[0]["path"], "Libraries/LibWeb/Crypto/CryptoAlgorithms.cpp")
        test_data_hotspots = audit["understanding"]["hotspot_groups"]["test_data"]
        self.assertEqual(test_data_hotspots[0]["path"], "Tests/LibWeb/Text/input/wpt-import/url/resources/IdnaTestV2.json")


if __name__ == "__main__":
    unittest.main()
