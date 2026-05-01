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
from classify import classifyFile, classifyRiskSurface  # noqa: E402



def _make_file(tmpdir: Path, rel_path: str, content: str = ""):
    full = tmpdir / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content or f"// {rel_path}\n", encoding="utf-8")


class RegressionFixtureTests(unittest.TestCase):
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

    def test_tensorflow_like_project_name_not_html(self):
        _make_file(self.project_root, "README.md",
                   '<div align="center"><img src="logo.png"/></div>\n\n# TensorFlow\n\nML framework.')
        _make_file(self.project_root, "tensorflow/core/ops.cc", "void Op() {}")
        _make_file(self.project_root, "tensorflow/python/keras/layers.py", "class Layer: pass")
        _make_file(self.project_root, "tensorflow/go/genop/main.go",
                   "package main\nfunc main() {}")
        _make_file(self.project_root, "tensorflow/compiler/xla/service.cc", "void Service() {}")

        audit, files = self._scan([".py", ".cc", ".go", ".md"])
        project_name = audit["understanding"]["project_name"]
        self.assertNotIn("<", project_name)
        self.assertNotIn("div", project_name.lower())
        self.assertNotIn("img", project_name.lower())

    def test_tensorflow_like_purpose_not_application_logic(self):
        _make_file(self.project_root, "README.md",
                   "# TensorFlow\napplication logic, application logic, application logic\n")
        _make_file(self.project_root, "tensorflow/core/ops.cc", "void Op() {}")
        _make_file(self.project_root, "tensorflow/python/keras/layers.py", "class Layer: pass")

        audit, files = self._scan([".py", ".cc", ".md"])
        purpose = audit["understanding"]["purpose"]
        self.assertNotIn("application logic", purpose.lower())
        self.assertNotEqual(purpose, "It is organized around application logic.")

    def test_test_files_not_in_runtime_risks(self):
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")
        _make_file(self.project_root, "src/main_test.py",
                   "def test_main(): pass\n")
        _make_file(self.project_root, "src/utils.cc",
                   "void util() {}\n")
        _make_file(self.project_root, "src/utils_test.cc",
                   "void test_util() {}\n")
        _make_file(self.project_root, "README.md", "# Test\n")

        audit, files = self._scan([".py", ".cc", ".md"])
        runtime_risks = audit["risk_groups"].get("runtime", [])
        runtime_files = {item["file"] for item in runtime_risks}
        self.assertNotIn("src/main_test.py", runtime_files)
        self.assertNotIn("src/utils_test.cc", runtime_files)

    def test_genop_is_generator_not_runtime(self):
        fc = classifyFile("tensorflow/go/genop/main.go")
        self.assertTrue(fc.isGenerator)

    def test_genop_not_in_runtime_entry_points(self):
        _make_file(self.project_root, "tensorflow/go/genop/main.go",
                   "package main\nfunc main() {}")
        _make_file(self.project_root, "tensorflow/core/ops.cc", "void Op() {}")
        _make_file(self.project_root, "README.md", "# TF\n")

        audit, files = self._scan([".go", ".cc", ".md"])

        runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
        generator_entries = audit["structure"]["entry_points_by_category"].get("generator", [])
        self.assertNotIn("tensorflow/go/genop/main.go", runtime_entries)
        self.assertIn("tensorflow/go/genop/main.go", generator_entries)

    def test_generated_files_not_in_runtime_risks(self):
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")
        _make_file(self.project_root, "src/gen/types.gen.ts", "export type User = {};\n")
        _make_file(self.project_root, "README.md", "# Test\n")

        audit, files = self._scan([".py", ".ts", ".md"])
        runtime_risks = audit["risk_groups"].get("runtime", [])
        runtime_files = {item["file"] for item in runtime_risks}
        self.assertNotIn("src/gen/types.gen.ts", runtime_files)

    def test_requirements_lock_is_dependency_lock_not_runtime(self):
        _make_file(self.project_root, "requirements_lock_3_10.txt",
                   "\n".join(f"pkg=={i}.0.0" for i in range(100)))
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")
        _make_file(self.project_root, "README.md", "# Test\n")

        audit, files = self._scan([".py", ".txt", ".md"])

        fc = classifyFile("requirements_lock_3_10.txt")
        self.assertTrue(fc.isDependencyLock)

        runtime_risks = audit["risk_groups"].get("runtime", [])
        for risk in runtime_risks:
            self.assertNotIn("requirements_lock", risk["file"])

        risk_groups = audit["risk_groups"]
        if "dependency_lock" in risk_groups:
            dep_files = {item["file"] for item in risk_groups["dependency_lock"]}
            self.assertIn("requirements_lock_3_10.txt", dep_files)

    def test_framework_library_workflow_not_app(self):
        _make_file(self.project_root, "tensorflow/core/ops.cc", "void Op() {}")
        _make_file(self.project_root, "tensorflow/python/keras/layers.py", "class Layer: pass")
        _make_file(self.project_root, "tensorflow/compiler/xla/service.cc", "void Service() {}")
        _make_file(self.project_root, "tensorflow/go/genop/main.go",
                   "package main\nfunc main() {}")
        _make_file(self.project_root, "README.md", "# TensorFlow\nAn ML framework.\n")

        audit, files = self._scan([".py", ".cc", ".go", ".md"])
        archetype = audit["understanding"]["archetype"]
        workflow = audit["understanding"]["workflow_hints"]
        self.assertEqual(archetype, "framework_library")
        self.assertTrue(
            any("Framework/library repo detected" in h for h in workflow),
            f"Expected framework_library workflow hint, got: {workflow}",
        )
        runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
        for entry in runtime_entries:
            self.assertNotIn("genop", entry)

    def test_ollama_like_framework_detection(self):
        _make_file(self.project_root, "llama/chat/chat.go",
                   "package chat\nfunc Chat() {}\n")
        _make_file(self.project_root, "llama/server/server.go",
                   "package server\nfunc Serve() {}\n")
        _make_file(self.project_root, "llama/api/types.go",
                   "package api\ntype Request struct{}\n")
        _make_file(self.project_root, "llama/cmd/llama/main.go",
                   "package main\nfunc main() {}\n")
        _make_file(self.project_root, "README.md", "# Ollama\n\nLLM runtime.\n")

        audit, files = self._scan([".go", ".md"])
        archetype = audit["understanding"]["archetype"]
        self.assertIn(archetype, ("app", "framework_library", "cli_server"))
        if archetype == "app":
            runtime_entries = audit["structure"]["entry_points_by_category"].get("runtime", [])
            self.assertTrue(
                any("llama/cmd/llama/main.go" in e for e in runtime_entries),
                f"Expected llama/cmd/llama/main.go in runtime entries, got: {runtime_entries}",
            )

    def test_monorepo_like_components_split(self):
        _make_file(self.project_root, "packages/app/src/main.ts", "console.log('app');\n")
        _make_file(self.project_root, "packages/sdk/src/client.ts", "export class Client {}\n")
        _make_file(self.project_root, "packages/server/src/index.ts", "import http from 'http';\n")
        _make_file(self.project_root, "packages/shared/src/utils.ts", "export const util = 1;\n")
        _make_file(self.project_root, "README.md", "# Monorepo\n")

        audit, files = self._scan([".ts", ".md"])
        components = {c["path"]: c["role"] for c in audit["understanding"]["main_components"]}
        self.assertIn("packages/app", components)
        self.assertIn("packages/sdk", components)
        self.assertIn("packages/server", components)

    def test_raw_html_readme_name_fallback(self):
        _make_file(self.project_root, "README.md",
                   '<div align="center">\n<p align="center">\n<img src="logo.png">\n</p>\n</div>\n')
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")

        audit, files = self._scan([".py", ".md"])
        project_name = audit["understanding"]["project_name"]
        self.assertNotIn("<", project_name)
        self.assertNotIn("div", project_name.lower())
        self.assertNotIn("img", project_name.lower())
        # Should be the repo folder name (no raw HTML)
        self.assertGreater(len(project_name), 0)
        self.assertNotEqual(project_name, '<div align="center">')

    def test_requirements_lock_file_issues_not_module_boundary(self):
        lock = "\n".join(f"pkg=={i}.0.0" for i in range(500))
        _make_file(self.project_root, "requirements_lock_3_10.txt", lock)
        _make_file(self.project_root, "src/app.py", "def main(): pass\n")
        _make_file(self.project_root, "README.md", "# App\n")

        audit, files = self._scan([".py", ".txt", ".md"])
        for issue in audit["issues"]:
            if "requirements_lock" in str(issue.get("file", "")):
                self.assertNotIn("module boundaries", issue["message"])
                self.assertIn("dependency", issue["message"].lower())

    def test_duplicate_factors_not_in_risk_scores(self):
        _make_file(self.project_root, "src/complex.py",
                   "import os\nimport sys\nimport json\n"
                   + "\n".join(f"def func{i}(): pass" for i in range(300))
                   + "\n")
        _make_file(self.project_root, "README.md", "# App\n")

        audit, files = self._scan([".py", ".md"])
        for risk in audit["risk_scores"]:
            factors = risk.get("factors", [])
            seen = set()
            for f in factors:
                self.assertNotIn(f.lower().strip(), seen,
                                 f"Duplicate factor '{f}' in {risk['file']}")
                seen.add(f.lower().strip())

    def test_tensorflow_components_split(self):
        _make_file(self.project_root, "tensorflow/core/framework/types.cc", "void Types() {}")
        _make_file(self.project_root, "tensorflow/core/ops/math_ops.cc", "void MathOps() {}")
        _make_file(self.project_root, "tensorflow/python/keras/layers.py", "class Layer: pass")
        _make_file(self.project_root, "tensorflow/python/keras/models.py", "class Model: pass")
        _make_file(self.project_root, "tensorflow/compiler/xla/service.cc", "void Service() {}")
        _make_file(self.project_root, "tensorflow/lite/kernels/add.cc", "void Add() {}")
        _make_file(self.project_root, "tensorflow/lite/kernels/mul.cc", "void Mul() {}")
        _make_file(self.project_root, "README.md", "# TF\n")

        audit, files = self._scan([".py", ".cc", ".md"])
        components = {c["path"]: c["role"] for c in audit["understanding"]["main_components"]}
        self.assertIn("tensorflow/core", components)
        self.assertIn("tensorflow/python", components)
        self.assertIn("tensorflow/compiler", components)
        self.assertIn("tensorflow/lite", components)

    def test_go_test_file_detection(self):
        _make_file(self.project_root, "src/server.go",
                   "package main\nfunc main() {}\n")
        _make_file(self.project_root, "src/server_test.go",
                   "package main\nfunc TestServer(t *testing.T) {}\n")
        _make_file(self.project_root, "README.md", "# App\n")

        audit, files = self._scan([".go", ".md"])

        fc = classifyFile("src/server_test.go")
        self.assertTrue(fc.isTest)

        runtime_risks = audit["risk_groups"].get("runtime", [])
        runtime_files = {item["file"] for item in runtime_risks}
        self.assertNotIn("src/server_test.go", runtime_files)

    def test_classify_surface_respects_classification(self):
        self.assertEqual(classifyRiskSurface("src/main.py"), "runtime")
        self.assertEqual(classifyRiskSurface("src/provider/models.ts"), "runtime")
        self.assertEqual(classifyRiskSurface("tests/test_main.py"), "test")
        self.assertEqual(classifyRiskSurface("docs/guide.md"), "documentation")
        self.assertEqual(classifyRiskSurface("vendor/lib.c"), "vendor")
        self.assertEqual(classifyRiskSurface("node_modules/express/index.js"), "vendor")
        self.assertEqual(classifyRiskSurface("third_party/foo/bar.py"), "vendor")
        self.assertEqual(classifyRiskSurface("packages/app/src/i18n/en.ts"), "localization")
        self.assertEqual(classifyRiskSurface("gen/types.gen.ts"), "generated_sdk")
        self.assertEqual(classifyRiskSurface("generated/client.go"), "generated_sdk")


    def test_gen_cc_not_in_runtime_risks(self):
        _make_file(self.project_root, "src/core.cc", "void Core() {}")
        _make_file(self.project_root, "src/converter_gen.cc", "// generated")
        _make_file(self.project_root, "README.md", "# Test\n")

        audit, files = self._scan([".cc", ".md"])
        runtime_risks = audit["risk_groups"].get("runtime", [])
        for risk in runtime_risks:
            self.assertNotIn("_gen.cc", risk["file"])
        all_risk_files = {r["file"] for r in audit.get("risk_scores", [])}
        self.assertNotIn("src/converter_gen.cc", all_risk_files)

    def test_framework_library_workflow_no_runtime_trace(self):
        _make_file(self.project_root, "tensorflow/core/ops.cc", "void Op() {}")
        _make_file(self.project_root, "tensorflow/python/layers.py", "class Layer: pass")
        _make_file(self.project_root, "tensorflow/go/genop/main.go",
                   "package main\nfunc main() {}")
        _make_file(self.project_root, "README.md", "# TensorFlow\nAn ML framework.\n")

        audit, files = self._scan([".py", ".cc", ".go", ".md"])
        workflow = audit["understanding"]["workflow_hints"]
        for hint in workflow:
            self.assertNotIn("Start runtime tracing from", hint)
        self.assertTrue(
            any("Framework/library repo detected" in h for h in workflow),
            f"Expected framework_library workflow hint, got: {workflow}",
        )

    def test_project_name_from_html_readme_fallback_dir_name(self):
        _make_file(self.project_root, "README.md",
                   '<p align="center"><img src="logo.png"/></p>\n')
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")

        audit, files = self._scan([".py", ".md"])
        project_name = audit["understanding"]["project_name"]
        self.assertNotIn("<", project_name)
        self.assertNotIn("div", project_name.lower())
        self.assertNotIn("<p", project_name.lower())
        self.assertEqual(project_name, self.project_root.name.replace("_", ""))

    def test_purpose_application_logic_replaced(self):
        _make_file(self.project_root, "src/main.py", "def main(): pass\n")

        from auditor import ProjectAuditor
        tempdir2 = tempfile.TemporaryDirectory()
        proj2 = Path(tempdir2.name)
        _make_file(proj2, "src/main.py", "def main(): pass\n")
        _make_file(proj2, "README.md", "# My Project\napplication logic, application logic, application logic\n")

        auditor2 = ProjectAuditor(str(proj2), str(proj2 / "checkpoints.json"))
        f2 = auditor2.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md"],
            max_size=1024 * 1024,
        )
        a2 = auditor2.audit_project(f2)
        purpose = a2["understanding"]["purpose"]
        self.assertNotIn("application logic", purpose.lower())
        self.assertIn("confidently inferred", purpose)
        tempdir2.cleanup()

    def test_large_requirements_lock_message_is_dependency(self):
        lock = "\n".join(f"pkg=={i}.0.0" for i in range(500))
        _make_file(self.project_root, "ci/requirements_lock_3_10.txt", lock)
        _make_file(self.project_root, "src/app.py", "def main(): pass\n")
        _make_file(self.project_root, "README.md", "# App\n")

        audit, files = self._scan([".py", ".txt", ".md"])
        fc = classifyFile("ci/requirements_lock_3_10.txt")
        self.assertTrue(fc.isDependencyLock)
        self.assertIn("dependency", fc.largeFilePolicy.lower())

    def test_template_like_framework_components_split_deep(self):
        _make_file(self.project_root, "tensorflow/core/framework/types.cc", "void Types() {}")
        _make_file(self.project_root, "tensorflow/core/ops/math_ops.cc", "void MathOps() {}")
        _make_file(self.project_root, "tensorflow/python/keras/layers.py", "class Layer: pass")
        _make_file(self.project_root, "tensorflow/python/keras/models.py", "class Model: pass")
        _make_file(self.project_root, "tensorflow/compiler/xla/service.cc", "void Service() {}")
        _make_file(self.project_root, "tensorflow/lite/kernels/add.cc", "void Add() {}")
        _make_file(self.project_root, "tensorflow/lite/kernels/mul.cc", "void Mul() {}")
        _make_file(self.project_root, "tensorflow/c/api_test.cc", "void TestApi() {}")
        _make_file(self.project_root, "tensorflow/c/c_api.cc", "void CApi() {}")
        _make_file(self.project_root, "README.md", "# TF\n")

        audit, files = self._scan([".py", ".cc", ".md"])
        components = {c["path"]: c["role"] for c in audit["understanding"]["main_components"]}
        self.assertIn("tensorflow/core", components)
        self.assertIn("tensorflow/python", components)
        self.assertIn("tensorflow/compiler", components)
        self.assertIn("tensorflow/lite", components)
        # tensorflow itself should NOT appear as a single "application logic" bucket
        for cpath, role in components.items():
            if cpath == "tensorflow" and "application" in role.lower():
                self.fail(f"tensorflow should be split, got role={role}")

    def test_score_file_risks_excludes_tests_and_generators(self):
        _make_file(self.project_root, "src/core.py", "def core(): pass\n")
        _make_file(self.project_root, "src/core_test.py", "def test_core(): pass\n")
        _make_file(self.project_root, "src/generate_protos.py", "def generate(): pass\n")
        _make_file(self.project_root, "src/gen/types.gen.ts", "export type T = {};\n")
        _make_file(self.project_root, "README.md", "# Test\n")

        audit, files = self._scan([".py", ".ts", ".md"])
        risk_scores = audit.get("risk_scores", [])
        risk_files = [r["file"] for r in risk_scores]
        self.assertIn("src/core.py", risk_files)
        self.assertNotIn("src/core_test.py", risk_files)
        self.assertNotIn("src/generate_protos.py", risk_files)
        self.assertNotIn("src/gen/types.gen.ts", risk_files)

    def test_genop_file_classified_as_generator_in_source_section(self):
        fc = classifyFile("tensorflow/go/genop/main.go")
        self.assertTrue(fc.isGenerator)

    def test_framework_library_project_type_is_mixed_language(self):
        _make_file(self.project_root, "tensorflow/core/ops.cc", "void Op() {}")
        _make_file(self.project_root, "tensorflow/python/layers.py", "class Layer: pass")
        _make_file(self.project_root, "tensorflow/compiler/xla/service.cc", "void Service() {}")
        _make_file(self.project_root, "tensorflow/lite/kernels/add.cc", "void Add() {}")
        _make_file(self.project_root, "README.md", "# TensorFlow\n")

        audit, files = self._scan([".py", ".cc", ".md"])
        project_type = audit["understanding"]["project_type"]
        self.assertIn("framework", project_type.lower())
        self.assertIn("learning", project_type.lower())

    def test_full_pipeline_tensorflow_readme_with_html_badges(self):
        readme = (
            '<div align="center">\n'
            '  <img src="logo.png"><br><br>\n'
            '</div>\n\n'
            '[![Python](https://img.shields.io/badge/Python-3.x-blue)]()\n'
            '[![Stars](https://img.shields.io/badge/stars-100k-green)]()\n\n'
            '# TensorFlow\n\n'
            'An end-to-end open source machine learning platform.\n'
        )
        _make_file(self.project_root, "README.md", readme)
        _make_file(self.project_root, "tensorflow/core/ops.cc", "void Op() {}")
        _make_file(self.project_root, "tensorflow/python/layers.py", "class Layer: pass")
        _make_file(self.project_root, "tensorflow/compiler/xla/service.cc", "void Service() {}")
        _make_file(self.project_root, "tensorflow/lite/kernels/add.cc", "void Add() {}")
        _make_file(self.project_root, "tensorflow/go/genop/main.go",
                   "package main\nfunc main() {}")

        audit, files = self._scan([".py", ".cc", ".go", ".md"])

        identity = audit["understanding"]
        self.assertNotIn("<", identity["project_name"])
        self.assertNotIn("div", identity["project_name"].lower())
        self.assertNotIn("align", identity["project_name"].lower())
        self.assertNotIn("img", identity["project_name"].lower())

        self.assertNotIn("<div", identity.get("summary", "").lower())
        self.assertNotIn("application logic, application logic",
                         identity.get("purpose", "").lower())
        self.assertEqual(identity["archetype"], "framework_library")

        runtime_eps = audit["structure"]["entry_points_by_category"].get("runtime", [])
        for ep in runtime_eps:
            self.assertNotIn("genop", ep)

    def test_normalize_identity_rejects_every_bad_pattern(self):
        auditor = ProjectAuditor(
            str(self.project_root),
            str(self.project_root / "checkpoints.json"),
        )
        result = auditor._normalize_identity(
            project_name='<div align="center">',
            project_type='c++ project and a test suite',
            purpose='It is organized around application logic, application logic, and application logic.',
            summary='<div align="center"> appears to be c++ project and a test suite. It is organized around application logic, application logic, and application logic.',
            description='<p>Some HTML here</p>',
        )
        self.assertNotIn("<", result["project_name"])
        self.assertNotIn("div", result["project_name"].lower())
        self.assertNotIn("application logic", result["purpose"].lower())
        self.assertNotIn("<div", result["summary"].lower())
        self.assertNotIn("<p>", result["description"].lower())
        self.assertNotIn("</p>", result["description"].lower())
        self.assertIn("confidently inferred", result["purpose"])
        self.assertEqual(result["project_name"], self.project_root.name)


if __name__ == "__main__":
    unittest.main()
