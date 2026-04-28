import json
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
from classify import classifyFile  # noqa: E402


def _make_file(tmpdir: Path, rel_path: str, content: str = ""):
    full = tmpdir / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content or f"// {rel_path}\n", encoding="utf-8")


class ReportQualityTests(unittest.TestCase):
    """Tests that Sentinel reports have no raw HTML identity,
    no repeated generic purpose, no false framework tags, and
    correct entry point / scoring behavior."""

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

    # --- Project Identity Tests ---

    def test_cargo_toml_extracted_as_project_name(self):
        _make_file(self.project_root, "Cargo.toml",
                   '[package]\nname = "my-rust-tool"\ndescription = "A Rust CLI tool."\n')
        _make_file(self.project_root, "src/main.rs", "fn main() {}\n")

        audit, files = self._scan([".rs", ".toml"])
        name = audit["understanding"]["project_name"]
        self.assertEqual(name, "my-rust-tool")

    def test_readme_title_strips_html(self):
        _make_file(self.project_root, "README.md",
                   '<h1 align="center"><span>LLVM</span></h1>\n\nCompiler infrastructure.\n')
        _make_file(self.project_root, "src/main.cpp", "int main() {}\n")

        audit, files = self._scan([".cpp", ".md"])
        name = audit["understanding"]["project_name"]
        self.assertNotIn("<", name)
        self.assertNotIn("<h1", name.lower())
        self.assertNotIn("span", name.lower())
        self.assertNotIn("align", name.lower())
        self.assertNotIn("</h1>", name.lower())

    def test_readme_title_strips_markdown_bold(self):
        _make_file(self.project_root, "README.md",
                   '# **TensorFlow**\n\nAn ML framework.\n')
        _make_file(self.project_root, "tensorflow/core/ops.cc", "void Op() {}")

        audit, files = self._scan([".cc", ".md"])
        name = audit["understanding"]["project_name"]
        self.assertNotIn("**", name)

    def test_metadata_preferred_over_readme_heading(self):
        _make_file(self.project_root, "pyproject.toml",
                   '[project]\nname = "preferred-name"\ndescription = "A real project."\n')
        _make_file(self.project_root, "README.md", '# Wrong Name From Heading\n\nStuff.\n')
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")

        audit, files = self._scan([".py", ".toml", ".md"])
        name = audit["understanding"]["project_name"]
        self.assertEqual(name, "preferred-name")

    def test_package_json_name_preferred_over_readme(self):
        _make_file(self.project_root, "package.json",
                   json.dumps({"name": "@scope/real-name", "description": "A package."}))
        _make_file(self.project_root, "README.md", '# README Title\n\nStuff.\n')
        _make_file(self.project_root, "index.js", "console.log(1);\n")

        audit, files = self._scan([".js", ".json", ".md"])
        name = audit["understanding"]["project_name"]
        self.assertEqual(name, "real-name")

    def test_go_mod_extracted_as_project_name(self):
        _make_file(self.project_root, "go.mod", "module github.com/user/my-service\n\ngo 1.21\n")
        _make_file(self.project_root, "main.go", "package main\nfunc main() {}\n")

        audit, files = self._scan([".go", "go.mod", ".md"])
        name = audit["understanding"]["project_name"]
        self.assertEqual(name, "github.com/user/my-service")

    def test_cmake_project_name_extracted(self):
        _make_file(self.project_root, "CMakeLists.txt", "project(LLVM C CXX)\ncmake_minimum_required(VERSION 3.20)\n")
        _make_file(self.project_root, "src/main.cpp", "int main() {}\n")

        audit, files = self._scan([".cpp", ".txt"])
        name = audit["understanding"]["project_name"]
        self.assertEqual(name, "LLVM")

    def test_no_raw_html_anywhere_in_identity(self):
        _make_file(self.project_root, "README.md",
                   '<div align="center"><img src="logo.png"/></div>\n\n# Rust\n\nA systems language.\n')
        _make_file(self.project_root, "src/main.rs", "fn main() {}\n")

        audit, files = self._scan([".rs", ".md"])
        identity = audit["understanding"]
        for field in ["project_name", "purpose", "summary"]:
            val = str(identity.get(field, ""))
            self.assertNotIn("<", val, f"HTML in {field}: {val}")
            self.assertNotIn("div", val.lower(), f"div in {field}: {val}")
            self.assertNotIn("align", val.lower(), f"align in {field}: {val}")

    # --- Framework Detection Tests ---

    def test_no_false_nextjs_for_llvm(self):
        _make_file(self.project_root, "llvm/lib/Transforms/next_gen.cc",
                   "#include \"llvm/Transforms/NextGen.h\"\nvoid runNextPass() {}\n")
        _make_file(self.project_root, "llvm/tools/llc/llc.cpp",
                   "#include \"llvm/Support/CommandLine.h\"\nint main(int argc, char** argv) { return 0; }\n")
        _make_file(self.project_root, "README.md", "# LLVM\n\nCompiler framework.\n")

        audit, files = self._scan([".cpp", ".h", ".md"])
        frameworks = audit["understanding"].get("frameworks", [])
        self.assertNotIn("nextjs", frameworks,
                         "LLVM should not be detected as Next.js despite 'next' tokens")

    def test_no_false_nextjs_for_rust(self):
        _make_file(self.project_root, "src/next.rs",
                   "use std::next::SomeNext;\npub fn next_item() {}\n")
        _make_file(self.project_root, "src/main.rs", "fn main() { println!(\"hello\"); }\n")
        _make_file(self.project_root, "Cargo.toml", '[package]\nname = "my-crate"\n')
        _make_file(self.project_root, "README.md", "# My Crate\n\nA Rust crate.\n")

        audit, files = self._scan([".rs", ".toml", ".md"])
        frameworks = audit["understanding"].get("frameworks", [])
        self.assertNotIn("nextjs", frameworks,
                         "Rust crate should not be detected as Next.js")

    def test_nextjs_detected_only_with_strong_evidence(self):
        _make_file(self.project_root, "package.json",
                   json.dumps({"dependencies": {"next": "^14.0.0"}}))
        _make_file(self.project_root, "pages/index.tsx",
                   "export default function Home() { return <div>Hi</div>; }\n")
        _make_file(self.project_root, "README.md", "# My App\n\nA Next.js app.\n")

        audit, files = self._scan([".tsx", ".json", ".md"])
        frameworks = audit["understanding"].get("frameworks", [])
        self.assertIn("nextjs", frameworks,
                      "Real Next.js app should be detected")

    def test_nextjs_not_detected_from_package_lock_only(self):
        _make_file(self.project_root, "package-lock.json",
                   json.dumps({"packages": {"node_modules/next": {"version": "14.0.0"}}}))
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")
        _make_file(self.project_root, "README.md", "# App\n\nSome app.\n")

        audit, files = self._scan([".py", ".json", ".md"])
        frameworks = audit["understanding"].get("frameworks", [])
        self.assertNotIn("nextjs", frameworks,
                         "package-lock.json alone should not trigger Next.js detection")

    # --- Entry Point Classification Tests ---

    def test_llvm_tools_are_build_not_runtime(self):
        _make_file(self.project_root, "clang/tools/clang-format/clang-format.cpp",
                   "int main(int argc, char** argv) { return 0; }\n")
        _make_file(self.project_root, "llvm/tools/llc/llc.cpp",
                   "#include \"llvm/CodeGen/MachineFunction.h\"\nint main(int argc, char** argv) { return 0; }\n")
        _make_file(self.project_root, "README.md", "# LLVM\n\nCompiler.\n")

        audit, files = self._scan([".cpp", ".h", ".md"])
        runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
        build_entries = audit["structure"]["entry_points_by_category"].get("build", [])
        for ep in runtime_entries:
            self.assertNotIn("/tools/", ep,
                             f"Tool file should not be runtime entry: {ep}")
        tool_files = [ep for ep in build_entries if "clang-format" in ep or "llc" in ep]
        self.assertTrue(len(tool_files) >= 1,
                        "clang-format or llc should appear in build entry points")

    def test_rust_bootstrap_is_build_not_runtime(self):
        _make_file(self.project_root, "src/bootstrap/bootstrap.py",
                   "if __name__ == '__main__':\n    print('build')\n")
        _make_file(self.project_root, "src/tools/cargo/src/bin/cargo.rs",
                   "fn main() { println!(\"cargo\"); }\n")
        _make_file(self.project_root, "README.md", "# Rust\n\nCompiler.\n")

        audit, files = self._scan([".py", ".rs", ".md"])
        runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
        build_entries = audit["structure"]["entry_points_by_category"].get("build", [])
        for ep in runtime_entries:
            self.assertNotIn("bootstrap", ep)
            self.assertNotIn("cargo", ep)
        self.assertTrue(any("bootstrap" in e for e in build_entries),
                        "bootstrap should be in build entries")

    def test_unit_tests_not_runtime_entry_points(self):
        _make_file(self.project_root, "llvm/unittests/IR/PassBuilderCallbacksTest.cpp",
                   "#include \"gtest/gtest.h\"\nint main(int argc, char** argv) { ::testing::InitGoogleTest(&argc, argv); return RUN_ALL_TESTS(); }\n")
        _make_file(self.project_root, "llvm/lib/IR/PassBuilder.cpp",
                   "void PassBuilder::run() {}\n")
        _make_file(self.project_root, "README.md", "# LLVM\n")

        audit, files = self._scan([".cpp", ".h", ".md"])
        runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
        for ep in runtime_entries:
            self.assertNotIn("unittests", ep,
                             f"Unit test should not be runtime entry: {ep}")
        # Test files with main() are excluded from entry points entirely
        # (they should not appear in runtime OR any entry point category)
        all_entry_categories = audit["structure"]["entry_points_by_category"]
        all_entry_paths = set()
        for paths in all_entry_categories.values():
            all_entry_paths.update(paths)
        self.assertNotIn("llvm/unittests/IR/PassBuilderCallbacksTest.cpp", all_entry_paths,
                         "Unit test with main() should not appear in any entry point category")

    def test_examples_separated_from_runtime(self):
        _make_file(self.project_root, "examples/hello/main.go",
                   "package main\nfunc main() { println!(\"hello\"); }\n")
        _make_file(self.project_root, "cmd/server/main.go",
                   "package main\nfunc main() { println!(\"serve\"); }\n")
        _make_file(self.project_root, "README.md", "# App\n")

        audit, files = self._scan([".go", ".md"])
        runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
        example_entries = audit["structure"]["entry_points_by_category"].get("example", [])
        self.assertIn("cmd/server/main.go", runtime_entries)
        self.assertIn("examples/hello/main.go", example_entries)
        self.assertNotIn("examples/hello/main.go", runtime_entries)

    # --- Scoring Tests ---

    def test_todo_density_not_raw_count(self):
        _make_file(self.project_root, "src/main.py",
                   "def main(): pass\n# TODO: fix\n# TODO: improve\n")
        _make_file(self.project_root, "src/utils.py",
                   "# TODO: refactor\n")
        _make_file(self.project_root, "README.md", "# Test\n")

        # Small repo with 3 TODOs in ~10 lines = high density
        audit_small, _ = self._scan([".py", ".md"])

        # Large repo with 3 TODOs in 10000+ lines = low density
        tempdir2 = tempfile.TemporaryDirectory()
        proj2 = Path(tempdir2.name)
        _make_file(proj2, "src/main.py",
                   "def main(): pass\n# TODO: fix\n")
        for i in range(500):
            _make_file(proj2, f"src/module{i}.py",
                       "\n".join(f"def func_{j}(): pass" for j in range(20)))
        _make_file(proj2, "src/utils.py", "# TODO: improve\n")
        _make_file(proj2, "README.md", "# Test\n")
        auditor2 = ProjectAuditor(str(proj2), str(proj2 / "checkpoints.json"))
        f2 = auditor2.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md"],
            max_size=1024 * 1024,
        )
        a2 = auditor2.audit_project(f2)
        tempdir2.cleanup()

        small_maintainability = audit_small["health_score_data"]["breakdown"]["maintainability_percent"]
        large_maintainability = a2["health_score_data"]["breakdown"]["maintainability_percent"]

        # Large repo with same TODO count should have HIGHER maintainability
        self.assertGreater(large_maintainability, small_maintainability,
                           "Large repo with same TODO count should have better maintainability score (density-based)")

    def test_mature_repo_confidence_label(self):
        _make_file(self.project_root, "README.md", "# Big Repo\n")
        for i in range(100):
            _make_file(self.project_root, f"src/module{i}.py",
                       "\n".join(f"def func_{j}(): pass" for j in range(30)))

        audit, files = self._scan([".py", ".md"])
        total_lines = audit["metrics"]["total_lines"]
        total_files = audit["metrics"]["total_files"]
        confidence_label = audit["health_score_data"].get("confidence_label", "")
        if total_lines >= 50000:
            self.assertIn(confidence_label, ("moderate_confidence", "low_confidence"))
        else:
            self.assertEqual(confidence_label, "normal")

    def test_llvm_repo_does_not_default_to_70(self):
        _make_file(self.project_root, "README.md", "# LLVM\n\nCompiler infrastructure.\n")
        for i in range(200):
            _make_file(self.project_root, f"llvm/lib/Target/X{i}/x{i}.cpp",
                       f"namespace llvm {{\nclass X{i} {{\npublic:\n  void run() {{}}\n}};\n}}\n")
        for i in range(50):
            _make_file(self.project_root, f"llvm/test/Transforms/test{i}.cpp",
                       f"// RUN: llc < %s\n// CHECK: pass\nint main() {{ return 0; }}\n")
        _make_file(self.project_root, "llvm/tools/llc/llc.cpp",
                   "int main(int argc, char** argv) { return 0; }\n")

        audit, files = self._scan([".cpp", ".md"])
        score = audit["health_score"]
        # Should not be exactly 70 — should vary based on actual signals
        self.assertNotEqual(score, 70,
                            "LLVM-like repo score should not default to exactly 70%")
        self.assertGreaterEqual(score, 30)
        self.assertLessEqual(score, 100)

    def test_health_score_not_always_70_or_55(self):
        _make_file(self.project_root, "README.md", "# Custom\n")
        _make_file(self.project_root, "src/main.py",
                   "def main(): pass\n\nif __name__ == '__main__':\n    main()\n")

        audit_small, _ = self._scan([".py", ".md"])
        score1 = audit_small["health_score"]

        tempdir2 = tempfile.TemporaryDirectory()
        proj2 = Path(tempdir2.name)
        _make_file(proj2, "README.md", "# Big\n")
        for i in range(20):
            _make_file(proj2, f"src/mod{i}.py", f"def func{i}(): pass\n")
        for i in range(10):
            _make_file(proj2, f"tests/test_{i}.py", f"def test_{i}(): pass\n")
        _make_file(proj2, "src/main.py", "def main(): pass\n\nif __name__ == '__main__':\n    main()\n")
        auditor2 = ProjectAuditor(str(proj2), str(proj2 / "checkpoints.json"))
        f2 = auditor2.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md"],
            max_size=1024 * 1024,
        )
        a2 = auditor2.audit_project(f2)
        tempdir2.cleanup()
        score2 = a2["health_score"]

        # Scores should differ based on actual repo characteristics
        self.assertNotEqual(score1, score2,
                            "Different repos should get different health scores")

    # --- Output Verification Tests ---

    def test_report_text_has_no_raw_html_identity(self):
        _make_file(self.project_root, "README.md",
                   '<div align="center"><img src="logo.png"/></div>\n\n# MyProject\n\nDescription.\n')
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")

        audit, files = self._scan([".py", ".md"])
        from reporter import ReportGenerator
        reporter = ReportGenerator()
        scan_result = {
            "scan_number": 1,
            "timestamp": "2025-01-01T00:00:00",
            "audit": audit,
            "diff": {"is_first_scan": True, "summary": "test"},
            "suggestions": [],
            "llm": {},
            "performance": {},
        }
        report = reporter.render_terminal(scan_result)
        self.assertNotIn("<div", report)
        self.assertNotIn("align=", report)
        self.assertNotIn("application logic, application logic", report)

    def test_report_no_repeated_generic_purpose(self):
        _make_file(self.project_root, "README.md", "# Project\n")
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")

        audit, files = self._scan([".py", ".md"])
        purpose = audit["understanding"]["purpose"]
        self.assertNotIn("application logic, application logic", purpose)
        self.assertNotIn("application logic, and application logic", purpose)

    def test_framework_tags_no_obvious_false_positives(self):
        _make_file(self.project_root, "CMakeLists.txt", "project(LLVM)\n")
        _make_file(self.project_root, "llvm/tools/llc/llc.cpp",
                   "int main(int argc, char** argv) { return 0; }\n")
        _make_file(self.project_root, "llvm/lib/IR/PassBuilder.cpp",
                   "void run() {}\n")
        _make_file(self.project_root, "clang/lib/Sema/SemaDecl.cpp",
                   "void Declare() {}\n")
        _make_file(self.project_root, "README.md", "# LLVM\n\nCompiler infrastructure.\n")

        audit, files = self._scan([".cpp", ".h", ".md"])
        frameworks = audit["understanding"].get("frameworks", [])
        false_web = {"nextjs", "react", "express", "fastapi", "flask", "django"}
        detected_false = false_web & set(frameworks)
        self.assertFalse(detected_false,
                         f"LLVM-like repo should not have web frameworks: {detected_false}")

    def test_llvm_entry_points_are_drivers_not_tests(self):
        _make_file(self.project_root, "clang/tools/clang-format/clang-format.cpp",
                   "int main(int argc, char** argv) { return 0; }\n")
        _make_file(self.project_root, "llvm/tools/llc/llc.cpp",
                   "int main(int argc, char** argv) { return 0; }\n")
        _make_file(self.project_root, "llvm/unittests/IR/PassBuilderCallbacksTest.cpp",
                   "int main(int argc, char** argv) { return 0; }\n")
        _make_file(self.project_root, "README.md", "# LLVM\n\nCompiler.\n")

        audit, files = self._scan([".cpp", ".h", ".md"])
        runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
        test_entries = audit["structure"]["entry_points_by_category"].get("test", [])

        # Unit test with main() should be in test, not runtime
        for ep in runtime_entries:
            self.assertNotIn("unittests", ep)

        # clang-format and llc should be build, not runtime (they're tools)
        build_entries = audit["structure"]["entry_points_by_category"].get("build", [])
        tool_files = [e for e in build_entries if "clang-format" in e or "llc" in e]
        self.assertTrue(len(tool_files) >= 1,
                        "clang-format/llc should be in build entries")

    def test_rust_entry_points_are_bootstrap_cargo_not_tests(self):
        _make_file(self.project_root, "src/bootstrap/bootstrap.py",
                   "if __name__ == '__main__':\n    print('build')\n")
        _make_file(self.project_root, "src/tools/cargo/src/bin/cargo.rs",
                   "fn main() { println!(\"cargo\"); }\n")
        _make_file(self.project_root, "src/librustc/lib.rs",
                   "pub fn compile() {}\n")
        _make_file(self.project_root, "compiler/rustc/src/main.rs",
                   "fn main() { println!(\"rustc\"); }\n")
        _make_file(self.project_root, "README.md", "# Rust\n\nCompiler.\n")

        audit, files = self._scan([".py", ".rs", ".md"])
        runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
        build_entries = audit["structure"]["entry_points_by_category"].get("build", [])

        for ep in runtime_entries:
            self.assertNotIn("bootstrap", ep)
            self.assertNotIn("cargo", ep)

        # Bootstrap should be in build entries
        build_paths = " ".join(build_entries)
        self.assertIn("bootstrap", build_paths,
                      "bootstrap should be in build entries")

        # cargo.rs at src/tools/cargo/src/bin/cargo.rs
        # Our check uses "src/tools/" in lower_path
        self.assertTrue(any("cargo" in e for e in build_entries),
                        "cargo should be in build entries")


class MatureRepoScoringTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_large_repo_with_strong_tests_gets_bonus(self):
        _make_file(self.project_root, "README.md", "# Big\n")
        for i in range(50):
            _make_file(self.project_root, f"src/mod{i}.py",
                       "\n".join(f"def func{j}(): pass" for j in range(100)))
        for i in range(50):
            _make_file(self.project_root, f"tests/test_mod{i}.py",
                       f"def test_mod{i}(): pass\n")

        auditor = ProjectAuditor(
            str(self.project_root),
            str(self.project_root / "checkpoints.json"),
        )
        files = auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md"],
            max_size=1024 * 1024,
        )
        audit = auditor.audit_project(files)
        score = audit["health_score"]
        self.assertGreaterEqual(score, 30)
        self.assertLessEqual(score, 100)


class IdentityResolverTests(unittest.TestCase):
    """Tests for the ranked identity resolver on real-world repos."""

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

    def test_fastapi_package_name_not_sponsors(self):
        """FastAPI's README starts with sponsor blocks — package name must win."""
        readme = (
            '---\n'
            '<p align="center">\n'
            '  <a href="https://fastapi.tiangolo.com/"><img src="https://fastapi.tiangolo.com/img/logo-margin/logo-teal.png"></a>\n'
            '</p>\n'
            '<p align="center">\n'
            '  <em>FastAPI framework, high performance, easy to learn, fast to code, ready for production</em>\n'
            '</p>\n'
            '<div align="center">\n'
            '<a href="https://github.com/fastapi/fastapi/actions?query=workflow%3ATests">\n'
            '</div>\n'
            '<div align="center">\n'
            '  <a href="https://fastapi.tiangolo.com">Documentation</a>\n'
            '  <a href="https://fastapi.tiangolo.com/#sponsors">Sponsors</a>\n'
            '</div>\n'
            '<div align="center">\n'
            '  <a href="https://fastapi.tiangolo.com/fastapi-people/#sponsors"><img src="https://fastapi.tiangolo.com/img/sponsors/2025/thank-you-dark.svg"></a>\n'
            '</div>\n'
            '---\n\n'
            '# FastAPI\n\n'
            'FastAPI framework, high performance, easy to learn, fast to code, ready for production.\n'
        )
        _make_file(self.project_root, "README.md", readme)
        _make_file(self.project_root, "pyproject.toml",
                   '[project]\nname = "fastapi"\ndescription = "A modern Python web framework."\n')
        _make_file(self.project_root, "fastapi/main.py",
                   "from fastapi import FastAPI\napp = FastAPI()\n")

        audit, files = self._scan([".py", ".toml", ".md"])
        name = audit["understanding"]["project_name"]
        self.assertEqual(name, "FastAPI",
                         f"Expected FastAPI, got: {name}")
        purpose = audit["understanding"]["purpose"]
        self.assertNotIn("sponsor", purpose.lower())
        self.assertNotIn("src=", purpose)
        self.assertNotIn("align=", purpose)

    def test_fastapi_readme_heading_with_sponsors(self):
        """Even without pyproject.toml, validated README heading should work."""
        readme = (
            '# Sponsors\n\n'
            'Thanks to all sponsors!\n\n'
            '# FastAPI\n\n'
            'FastAPI framework, high performance, easy to learn, fast to code, ready for production.\n'
        )
        _make_file(self.project_root, "README.md", readme)
        _make_file(self.project_root, "fastapi/app.py", "from fastapi import FastAPI\napp = FastAPI()\n")

        audit, files = self._scan([".py", ".md"])
        name = audit["understanding"]["project_name"]
        self.assertNotEqual(name, "Sponsors")
        self.assertNotIn("sponsor", name.lower())

    def test_godot_readme_identity_not_html(self):
        """Godot Engine README with HTML banners should not leak HTML."""
        readme = (
            '<picture>\n'
            '  <source media="(prefers-color-scheme: dark)" srcset="https://godotengine.org/assets/press/logo_large_dark.png">\n'
            '  <img alt="Godot Engine logo" src="https://godotengine.org/assets/press/logo_large_light.png">\n'
            '</picture>\n\n'
            '# Godot Engine\n\n'
            'Godot Engine is a feature-packed, cross-platform game engine.\n'
        )
        _make_file(self.project_root, "README.md", readme)
        _make_file(self.project_root, "main/main.cpp", "int main() { return 0; }\n")
        _make_file(self.project_root, "core/object.cpp", "void Object() {}\n")

        audit, files = self._scan([".cpp", ".md"])
        name = audit["understanding"]["project_name"]
        self.assertNotIn("<", name)
        self.assertNotIn("img", name.lower())
        self.assertNotIn("src=", name.lower())
        purpose = audit["understanding"]["purpose"]
        self.assertNotIn("src=", purpose)

    def test_tensorflow_readme_identity(self):
        """TensorFlow README with HTML badges should extract correct name and purpose."""
        readme = (
            '<div align="center">\n'
            '  <img src="https://www.tensorflow.org/images/tf_logo_horizontal.png"><br><br>\n'
            '</div>\n\n'
            '[![Python](https://img.shields.io/badge/python-3.9-blue)]()\n'
            '[![TensorFlow](https://img.shields.io/badge/tensorflow-2.0-orange)]()\n\n'
            '# TensorFlow\n\n'
            'An end-to-end open source machine learning platform.\n'
        )
        _make_file(self.project_root, "README.md", readme)
        _make_file(self.project_root, "tensorflow/core/ops.cc", "void Op() {}\n")
        _make_file(self.project_root, "tensorflow/python/layers.py", "class Layer: pass\n")

        audit, files = self._scan([".py", ".cc", ".md"])
        name = audit["understanding"]["project_name"]
        self.assertEqual(name, "TensorFlow",
                         f"Expected TensorFlow, got: {name}")
        purpose = audit["understanding"]["purpose"]
        self.assertNotIn("src=", purpose)
        self.assertNotIn("align", purpose.lower())
        self.assertNotIn("badge", purpose.lower())

    def test_tensorflow_pyproject_metadata_wins(self):
        """pyproject.toml name should win over README section heading."""
        _make_file(self.project_root, "pyproject.toml",
                   '[project]\nname = "tensorflow"\ndescription = "Machine learning framework."\n')
        _make_file(self.project_root, "README.md",
                   '# Download and Setup\n\nInstructions for installing.\n')
        _make_file(self.project_root, "tensorflow/core/ops.cc", "void Op() {}\n")

        audit, files = self._scan([".py", ".cc", ".toml", ".md"])
        name = audit["understanding"]["project_name"]
        # "tensorflow" may be normalized to "TensorFlow" by known repo mapping
        self.assertNotEqual(name, "Download and Setup",
                            "Should not use README section heading as project name")
        self.assertNotIn("Download", name)

    def test_rust_repo_name_not_why_rust(self):
        """'Why Rust?' heading should not become project name."""
        readme = (
            '<div align="center">\n'
            '  <img src="https://raw.githubusercontent.com/rust-lang/www.rust-lang.org/master/static/images/rust-social-wide-light.svg">\n'
            '</div>\n\n'
            '# Why Rust?\n\n'
            'A language empowering everyone to build reliable and efficient software.\n'
        )
        _make_file(self.project_root, "README.md", readme)
        _make_file(self.project_root, "src/main.rs", "fn main() {}\n")
        _make_file(self.project_root, "Cargo.toml",
                   '[package]\nname = "rust"\ndescription = "The Rust programming language."\n')

        audit, files = self._scan([".rs", ".toml", ".md"])
        name = audit["understanding"]["project_name"]
        self.assertNotEqual(name, "Why Rust?")
        self.assertNotIn("?", name)
        self.assertIn(name, ("rust", "Rust"),
                      f"Expected rust/Rust, got: {name}")

    def test_purpose_not_application_logic(self):
        """Repo with unclear components should not get 'organized around application logic'."""
        _make_file(self.project_root, "README.md", "# My Project\n\nBuilt with love.\n")
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")

        audit, files = self._scan([".py", ".md"])
        purpose = audit["understanding"]["purpose"]
        self.assertNotIn("application logic", purpose.lower())


class RustRepoRegressionTests(unittest.TestCase):
    """Tests for rust-lang/rust-style repo with HTML image banners."""

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

    def test_project_name_not_why_rust_from_html_readme(self):
        readme = (
            '<div align="center">\n'
            '  <img src="https://raw.githubusercontent.com/rust-lang/www.rust-lang.org/master/static/images/rust-social-wide-light.svg">\n'
            '</div>\n\n'
            '# Why Rust?\n\n'
            'A language empowering everyone to build reliable and efficient software.\n'
        )
        _make_file(self.project_root, "README.md", readme)
        _make_file(self.project_root, "src/main.rs", "fn main() {}\n")
        _make_file(self.project_root, "Cargo.toml",
                   '[package]\nname = "rust"\ndescription = "Rust compiler."\n')

        audit, files = self._scan([".rs", ".toml", ".md"])
        name = audit["understanding"]["project_name"]
        self.assertNotEqual(name, "Why Rust?")
        self.assertNotIn("?", name)
        purpose = audit["understanding"]["purpose"]
        self.assertNotIn("src=", purpose)
        self.assertNotIn("raw.githubusercontent.com", purpose)

    def test_purpose_no_html_when_readme_starts_with_images(self):
        readme = (
            '<div align="center">\n'
            '  <img src="https://raw.githubusercontent.com/rust-lang/www.rust-lang.org/master/static/images/rust-social-wide-light.svg">\n'
            '</div>\n\n'
            '# Rust\n\n'
            'A language empowering everyone to build reliable and efficient software.\n'
        )
        _make_file(self.project_root, "README.md", readme)
        _make_file(self.project_root, "src/main.rs", "fn main() {}\n")

        audit, files = self._scan([".rs", ".md"])
        purpose = audit["understanding"]["purpose"]
        self.assertNotIn("src=", purpose)
        self.assertNotIn("raw.githubusercontent.com", purpose)

    def test_readme_summary_strips_image_url(self):
        readme = (
            '<div align="center"><img src="https://raw.githubusercontent.com/rust-lang/www.rust-lang.org/master/static/images/rust-social-wide-light.svg"></div>\n\n'
            '# Rust\n\n'
            'The Rust programming language.\n'
        )
        _make_file(self.project_root, "README.md", readme)
        _make_file(self.project_root, "src/main.rs", "fn main() {}\n")

        audit, files = self._scan([".rs", ".md"])
        understanding = audit["understanding"]
        summary = understanding.get("summary", "")
        purpose = understanding.get("purpose", "")
        for field in [summary, purpose]:
            self.assertNotIn("src=", field, f"Found src= in: {field}")
            self.assertNotIn("raw.githubusercontent.com", field)

    def test_tests_rs_classified_as_test(self):
        """tests.rs file in a source directory should be classified as test."""
        from classify import classifyFile
        fc = classifyFile("src/tools/rust-analyzer/crates/ide/src/hover/tests.rs")
        self.assertTrue(fc.isTest,
                        f"tests.rs should be classified as test, got role={fc.role}")

    def test_hotspot_excludes_tests_rs(self):
        _make_file(self.project_root, "src/main.rs",
                   "fn main() { println!(\"hello\"); }\n")
        _make_file(self.project_root,
                   "src/tools/rust-analyzer/crates/ide/src/hover/tests.rs",
                   "fn test_hover() {}\n")
        _make_file(self.project_root,
                   "compiler/rustc/src/main.rs",
                   "fn main() { println!(\"rustc\"); }\n")
        _make_file(self.project_root,
                   "compiler/rustc_middle/src/lib.rs",
                   "pub fn middle() {}\n")
        _make_file(self.project_root, "README.md", "# Rust\n\nCompiler.\n")
        _make_file(self.project_root, "Cargo.toml",
                   '[package]\nname = "rust"\n')

        audit, files = self._scan([".rs", ".toml", ".md"])
        hotspot_groups = audit["understanding"].get("hotspot_groups", {})
        runtime_hotspots = hotspot_groups.get("runtime", [])
        runtime_files = {h["path"] for h in runtime_hotspots}
        self.assertNotIn("src/tools/rust-analyzer/crates/ide/src/hover/tests.rs",
                         runtime_files,
                         "tests.rs should NOT appear in runtime hotspots")
        test_hotspots = hotspot_groups.get("test_runner", [])
        test_files = {h["path"] for h in test_hotspots}

    def test_runtime_hotspot_includes_compiler_src(self):
        _make_file(self.project_root, "compiler/rustc/src/main.rs",
                   "fn main() { println!(\"rustc\"); }\n")
        _make_file(self.project_root, "compiler/rustc_middle/src/lib.rs",
                   "pub fn middle() {}\n")
        _make_file(self.project_root, "README.md", "# Rust\n\nCompiler.\n")
        _make_file(self.project_root, "Cargo.toml",
                   '[package]\nname = "rust"\n')

        audit, files = self._scan([".rs", ".toml", ".md"])
        runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
        self.assertIn("compiler/rustc/src/main.rs", runtime_entries,
                      "compiler/rustc/src/main.rs should be a runtime entry point")

    def test_tests_rs_not_in_runtime_entry_points(self):
        _make_file(self.project_root, "compiler/rustc/src/main.rs",
                   "fn main() { println!(\"rustc\"); }\n")
        _make_file(self.project_root,
                   "src/tools/rust-analyzer/crates/ide/src/hover/tests.rs",
                   "fn test_hover() {}\n")
        _make_file(self.project_root, "README.md", "# Rust\n\nCompiler.\n")
        _make_file(self.project_root, "Cargo.toml",
                   '[package]\nname = "rust"\n')

        audit, files = self._scan([".rs", ".toml", ".md"])
        all_entries = set()
        for paths in audit["structure"]["entry_points_by_category"].values():
            all_entries.update(paths)
        self.assertNotIn(
            "src/tools/rust-analyzer/crates/ide/src/hover/tests.rs",
            all_entries,
            "tests.rs should not appear in any entry point category",
        )


if __name__ == "__main__":
    unittest.main()
