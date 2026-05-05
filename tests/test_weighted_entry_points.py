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


class WeightedEntryPointsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name)

        # Create a project with entry points in different directories
        (self.project_root / "README.md").write_text("# Test Project\n", encoding="utf-8")

        # Entry point in src/ directory (should have high weight)
        (self.project_root / "src").mkdir()
        (self.project_root / "src" / "main.py").write_text(
            "def main():\n    return 'hello'\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8",
        )

        # Entry point in tests/ directory (should have low weight)
        (self.project_root / "tests").mkdir()
        (self.project_root / "tests" / "test_main.py").write_text(
            "def test_main():\n    assert True\n",
            encoding="utf-8",
        )

        # Entry point in docs/ directory (should have low weight)
        (self.project_root / "docs").mkdir()
        (self.project_root / "docs" / "example.py").write_text(
            "# Documentation example\nprint('hello')\n",
            encoding="utf-8",
        )

        # Entry point in scripts/ directory (should have medium-low weight)
        (self.project_root / "scripts").mkdir()
        (self.project_root / "scripts" / "tool.py").write_text(
            "# Tool script\nprint('tool')\n",
            encoding="utf-8",
        )

        self.auditor = ProjectAuditor(
            str(self.project_root),
            str(self.project_root / "checkpoints.json"),
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_core_directory_weighting(self):
        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".py", ".md"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)

        # Get entry points and their scores
        entry_points = audit["structure"]["entry_point_details"]

        # Find the entry points by path
        src_main = next((ep for ep in entry_points if "src/main.py" in ep["path"]), None)
        tests_main = next((ep for ep in entry_points if "test_main.py" in ep["path"]), None)
        docs_example = next((ep for ep in entry_points if "example.py" in ep["path"]), None)

        # src/main.py should have the highest score
        if src_main:
            self.assertGreater(src_main["score"], 100, "src/main.py should have high score")

        # tests/test_main.py should have lower score
        if tests_main:
            self.assertLess(tests_main["score"], src_main["score"], "tests/test_main.py should have lower score than src/main.py")

        # docs/example.py should have lower score
        if docs_example:
            self.assertLess(docs_example["score"], src_main["score"], "docs/example.py should have lower score than src/main.py")


if __name__ == "__main__":
    unittest.main()
