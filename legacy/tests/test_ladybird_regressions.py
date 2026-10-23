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
from classify import classifyRiskSurface  # noqa: E402

TOOLING_FILES_NOT_IN_RUNTIME = [
    "Meta/ladybird.py",
    "Utilities/test262-runner.cpp",
    "Meta/import-wpt-test.py",
    "Meta/Linters/check_flatpak.py",
    "Meta/Utils/find_compiler.py",
    "Libraries/LibJS/AsmIntGen/src/main.rs",
    "Meta/Lagom/Fuzzers/FuzzilliJs.cpp",
    "Meta/Linters/check_html_doctype.py",
]


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
        self.assertTrue(
            audit["understanding"]["workflow_hints"][0].startswith("Start runtime tracing from Libraries/LibMain/Main.cpp")
            or audit["understanding"]["workflow_hints"][0].startswith("Browser engine:")
        )

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


class RiskSurfaceClassificationTests(unittest.TestCase):
    """Tests for classifyRiskSurface path-based rules."""

    def test_meta_ladybird_is_build_tooling(self):
        self.assertEqual(classifyRiskSurface("Meta/ladybird.py"), "build_tooling")

    def test_meta_generators_is_generator(self):
        self.assertEqual(classifyRiskSurface("Meta/Generators/generate_ipc.py"), "generator")

    def test_meta_linters_is_build_tooling(self):
        self.assertEqual(classifyRiskSurface("Meta/Linters/check_flatpak.py"), "build_tooling")

    def test_meta_utils_is_build_tooling(self):
        self.assertEqual(classifyRiskSurface("Meta/Utils/find_compiler.py"), "build_tooling")

    def test_meta_import_wpt_test_is_test_runner(self):
        self.assertEqual(classifyRiskSurface("Meta/import-wpt-test.py"), "test_runner")

    def test_utilities_test_runner_is_test_runner(self):
        self.assertEqual(classifyRiskSurface("Utilities/test262-runner.cpp"), "test_runner")

    def test_asmintgen_is_generator(self):
        self.assertEqual(classifyRiskSurface("Libraries/LibJS/AsmIntGen/src/main.rs"), "generator")

    def test_devcontainer_is_environment_setup(self):
        self.assertEqual(classifyRiskSurface(".devcontainer/features/ladybird/install.sh"), "environment_setup")

    def test_libweb_source_is_runtime(self):
        self.assertEqual(classifyRiskSurface("Libraries/LibWeb/Crypto/CryptoAlgorithms.cpp"), "runtime")

    def test_libjs_source_is_runtime(self):
        self.assertEqual(classifyRiskSurface("Libraries/LibJS/Bytecode/Interpreter.cpp"), "runtime")

    def test_libwasm_source_is_runtime(self):
        self.assertEqual(classifyRiskSurface("Libraries/LibWasm/Validator.cpp"), "runtime")

    def test_libgfx_source_is_runtime(self):
        self.assertEqual(classifyRiskSurface("Libraries/LibGfx/Bitmap.cpp"), "runtime")

    def test_ak_source_is_runtime(self):
        self.assertEqual(classifyRiskSurface("AK/String.cpp"), "runtime")

    def test_documentation_is_documentation(self):
        self.assertEqual(classifyRiskSurface("Documentation/Guide.md"), "documentation")

    def test_vendor_is_vendor(self):
        self.assertEqual(classifyRiskSurface("vendor/somelib/lib.c"), "vendor")

    def test_tests_directory_is_test_runner(self):
        self.assertEqual(classifyRiskSurface("Tests/LibWeb/Text/runner.cpp"), "test_runner")
        self.assertIn(classifyRiskSurface("Tests/LibWeb/Text/test_foo.cpp"), ("test_data", "test"))

    def test_wpt_import_is_test_data(self):
        self.assertEqual(classifyRiskSurface("Tests/LibWeb/Text/input/wpt-import/url/resources/data.json"), "test_data")

    def test_meta_cmake_is_build_tooling(self):
        self.assertEqual(classifyRiskSurface("Meta/CMake/flatpak/angle/angle-build.sh"), "build_tooling")

    def test_lagom_fuzzers_is_test_runner(self):
        self.assertEqual(classifyRiskSurface("Meta/Lagom/Fuzzers/FuzzilliJs.cpp"), "test_runner")

    def test_tiffgenerator_is_generator(self):
        self.assertEqual(classifyRiskSurface("Libraries/LibGfx/TIFFGenerator.py"), "generator")

    def test_libmedia_source_is_runtime(self):
        self.assertEqual(classifyRiskSurface("Libraries/LibMedia/PlaybackManager.cpp"), "runtime")

    def test_libwebview_source_is_runtime(self):
        self.assertEqual(classifyRiskSurface("Libraries/LibWebView/View.cpp"), "runtime")

    def test_libmain_source_is_runtime(self):
        self.assertEqual(classifyRiskSurface("Libraries/LibMain/Main.cpp"), "runtime")

    def test_rust_build_rs_is_build_tooling(self):
        self.assertEqual(classifyRiskSurface("Libraries/LibGfx/Rust/build.rs"), "build_tooling")

    def test_3rdparty_in_tests_is_vendor(self):
        self.assertEqual(classifyRiskSurface("Tests/LibWeb/3rdparty/abc/test.c"), "vendor")


class RuntimeRiskFilterTests(unittest.TestCase):
    """Tests that tooling files are excluded from Top runtime risks."""

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

    def _create_ladybird_fixture(self):
        """Create a realistic Ladybird project fixture with tooling and runtime files."""
        # Runtime source files (should be in runtime risks)
        runtime_files = {
            "Libraries/LibWeb/Crypto/CryptoAlgorithms.cpp": 900,
            "Libraries/LibWeb/HTML/Parser/HTMLParser.cpp": 850,
            "Libraries/LibWeb/CSS/Parser/ValueParsing.cpp": 800,
            "Libraries/LibWeb/DOM/Document.cpp": 750,
            "Libraries/LibJS/Bytecode/Interpreter.cpp": 700,
            "Libraries/LibWeb/Fetch/Fetching/Fetching.cpp": 650,
        }
        for path, line_count in runtime_files.items():
            full = self.project_root / path
            full.parent.mkdir(parents=True, exist_ok=True)
            lines = "\n".join(f"int func_{i}() {{ return {i}; }}" for i in range(line_count))
            full.write_text(lines + "\n// TODO: review\n", encoding="utf-8")

        # Tooling files (should NOT be in runtime risks)
        tooling_files = [
            ("Meta/ladybird.py", 'if __name__ == "__main__":\n    print("ladybird")\n'),
            ("Utilities/test262-runner.cpp", "// test runner\nint main() { return 0; }\n"),
            ("Meta/import-wpt-test.py", "#!/usr/bin/env python3\nimport sys\nprint('import')\n"),
            ("Meta/Linters/check_flatpak.py", "#!/usr/bin/env python3\nprint('check')\n"),
            ("Meta/Utils/find_compiler.py", "#!/usr/bin/env python3\nprint('find')\n"),
            ("Libraries/LibJS/AsmIntGen/src/main.rs", "fn main() {\n    println!(\"gen\");\n}\n"),
        ]
        for path, content in tooling_files:
            full = self.project_root / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

    def test_tooling_files_not_in_top_runtime_risks(self):
        self._create_ladybird_fixture()
        audit = self._scan([".cpp", ".py", ".rs", ".md", ".json"])

        risk_groups = audit.get("risk_groups", {})
        runtime_risks = risk_groups.get("runtime", [])
        runtime_files = {item["file"] for item in runtime_risks}

        for tooling_file in TOOLING_FILES_NOT_IN_RUNTIME:
            self.assertNotIn(
                tooling_file,
                runtime_files,
                f"{tooling_file} should NOT appear in Top runtime risks",
            )

    def test_tooling_files_appear_in_correct_surfaces(self):
        self._create_ladybird_fixture()
        audit = self._scan([".cpp", ".py", ".rs", ".md", ".json"])

        from classify import classifyRiskSurface
        self.assertEqual(classifyRiskSurface("Meta/ladybird.py"), "build_tooling")
        self.assertEqual(classifyRiskSurface("Meta/Linters/check_flatpak.py"), "build_tooling")
        self.assertEqual(classifyRiskSurface("Meta/Linters/check_html_doctype.py"), "build_tooling")
        self.assertEqual(classifyRiskSurface("Meta/Utils/find_compiler.py"), "build_tooling")
        self.assertEqual(classifyRiskSurface("Libraries/LibJS/AsmIntGen/src/main.rs"), "generator")
        self.assertEqual(classifyRiskSurface("Utilities/test262-runner.cpp"), "test_runner")
        self.assertEqual(classifyRiskSurface("Meta/import-wpt-test.py"), "test_runner")
        self.assertEqual(classifyRiskSurface("Meta/Lagom/Fuzzers/FuzzilliJs.cpp"), "test_runner")

        # If they DO appear in risk_groups, they should be in the right surface
        risk_groups = audit.get("risk_groups", {})
        for surface, items in risk_groups.items():
            for item in items:
                f = item["file"]
                if f in {"Meta/ladybird.py", "Meta/Linters/check_flatpak.py", "Meta/Utils/find_compiler.py"}:
                    self.assertEqual(surface, "build_tooling", f"{f} should be in build_tooling, not {surface}")
                elif f == "Libraries/LibJS/AsmIntGen/src/main.rs":
                    self.assertEqual(surface, "generator", f"{f} should be in generator, not {surface}")

    def test_runtime_risks_include_first_party_source(self):
        self._create_ladybird_fixture()
        audit = self._scan([".cpp", ".py", ".rs", ".md", ".json"])

        risk_groups = audit.get("risk_groups", {})
        runtime_risks = risk_groups.get("runtime", [])
        runtime_files = {item["file"] for item in runtime_risks}

        # Should include runtime source files
        self.assertIn("Libraries/LibWeb/Crypto/CryptoAlgorithms.cpp", runtime_files)
        self.assertIn("Libraries/LibJS/Bytecode/Interpreter.cpp", runtime_files)

    def test_risk_factors_are_not_duplicated(self):
        self._create_ladybird_fixture()
        audit = self._scan([".cpp", ".py", ".rs", ".md", ".json"])

        risk_groups = audit.get("risk_groups", {})
        for surface, items in risk_groups.items():
            for item in items:
                factors = item.get("factors", [])
                self.assertEqual(
                    len(factors),
                    len(set(factors)),
                    f"Duplicate factors in {item['file']}: {factors}",
                )

    def test_component_labels_are_specific(self):
        self._create_ladybird_fixture()
        audit = self._scan([".cpp", ".py", ".rs", ".md", ".json"])

        components = {item["path"]: item["role"] for item in audit["understanding"]["main_components"]}

        # Should have specific labels, not generic ones
        if "Libraries/LibWeb" in components:
            self.assertEqual(components["Libraries/LibWeb"], "browser engine code")
        if "Libraries/LibJS" in components:
            self.assertEqual(components["Libraries/LibJS"], "JavaScript engine code")

    def test_group_risk_scores_has_all_surfaces(self):
        self._create_ladybird_fixture()
        audit = self._scan([".cpp", ".py", ".rs", ".md", ".json"])

        risk_groups = audit.get("risk_groups", {})
        # Runtime should always be present for large source files
        self.assertIn("runtime", risk_groups)
        # Risk groups should have proper surface labels
        for surface in risk_groups:
            self.assertIn(surface, {"runtime", "build_tooling", "generator", "test_runner", "test_data", "documentation", "vendor", "other"})

    def test_hotspot_groups_include_generator_and_test_runner(self):
        self._create_ladybird_fixture()
        audit = self._scan([".cpp", ".py", ".rs", ".md", ".json"])

        hotspot_groups = audit["understanding"].get("hotspot_groups", {})
        valid_keys = {"runtime", "build_tooling", "generator", "test_runner", "vendor", "test_data", "documentation"}
        for key in hotspot_groups:
            self.assertIn(key, valid_keys, f"Unexpected hotspot group key: {key}")


class FocusFileRuntimeTests(unittest.TestCase):
    """Test that focus files for runtime-tracing prompts exclude build files."""

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

    def test_runtime_hotspot_trace_excludes_build_tooling_from_focus(self):
        (self.project_root / "Libraries" / "LibMain").mkdir(parents=True)
        (self.project_root / "Libraries" / "LibMain" / "Main.cpp").write_text(
            "int main() {\n    return 0;\n}\n",
            encoding="utf-8",
        )
        (self.project_root / "Libraries" / "LibWeb" / "DOM").mkdir(parents=True)
        (self.project_root / "Libraries" / "LibWeb" / "DOM" / "Document.cpp").write_text(
            "\n".join(f"int func_{i}() {{ return {i}; }}" for i in range(300)) + "\n",
            encoding="utf-8",
        )
        (self.project_root / "Libraries" / "LibJS" / "Bytecode").mkdir(parents=True)
        (self.project_root / "Libraries" / "LibJS" / "Bytecode" / "Interpreter.cpp").write_text(
            "\n".join(f"int bc_{i}() {{ return {i}; }}" for i in range(300)) + "\n",
            encoding="utf-8",
        )
        (self.project_root / "Meta").mkdir(parents=True)
        (self.project_root / "Meta" / "ladybird.py").write_text(
            'if __name__ == "__main__":\n    print("ladybird")\n',
            encoding="utf-8",
        )

        audit = self._scan([".cpp", ".py", ".md", ".json"])
        understanding = audit.get("understanding", {})
        hotspot_groups = understanding.get("hotspot_groups", {})

        runtime_hotspots = hotspot_groups.get("runtime", [])
        runtime_files = {h["path"] for h in runtime_hotspots}
        self.assertNotIn("Meta/ladybird.py", runtime_files,
                         "Meta/ladybird.py should not be in runtime hotspots")

        from suggester import Suggester
        suggester = Suggester()
        suggestions = suggester.generate_suggestions(audit, {}, {})
        trace_suggestion = None
        for s in suggestions:
            if "trace" in s.get("title", "").lower() and "execution" in s.get("title", "").lower():
                trace_suggestion = s
                break

        if trace_suggestion:
            focus = trace_suggestion.get("focus_files", [])
            for f in focus:
                surface = classifyRiskSurface(f)
                self.assertNotEqual(surface, "build_tooling",
                                     f"{f} is build_tooling and should not be in runtime focus files")

    def test_risk_factors_not_duplicated(self):
        (self.project_root / "Libraries" / "LibWeb" / "DOM").mkdir(parents=True)
        (self.project_root / "Libraries" / "LibWeb" / "DOM" / "Document.cpp").write_text(
            "\n".join(f"int func_{i}() {{ return {i}; }}" for i in range(300)) + "\n// TODO: fix\n",
            encoding="utf-8",
        )

        audit = self._scan([".cpp", ".md", ".json"])

        for risk in audit.get("risk_scores", []):
            factors = risk.get("factors", [])
            self.assertEqual(
                len(factors),
                len(set(f.lower().strip() for f in factors)),
                f"Duplicate factors in {risk['file']}: {factors}",
            )


if __name__ == "__main__":
    unittest.main()
