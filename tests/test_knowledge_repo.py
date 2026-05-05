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


class KnowledgeRepoTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name)

        # Create a knowledge/artifact repo structure
        (self.project_root / "README.md").write_text(
            "# AI Skill Library\n\nThis repo contains examples and documentation.\n",
            encoding="utf-8",
        )

        # Create multiple markdown files with code blocks
        (self.project_root / "concept_1.md").write_text(
            "# Concept 1\n\n```python\ndef example():\n    return 'hello'\n```\n",
            encoding="utf-8",
        )
        (self.project_root / "concept_2.md").write_text(
            "# Concept 2\n\n```sql\nSELECT * FROM table;\n```\n",
            encoding="utf-8",
        )
        (self.project_root / "concept_3.md").write_text(
            "# Concept 3\n\n```json\n{'key': 'value'}\n```\n",
            encoding="utf-8",
        )
        (self.project_root / "concept_4.md").write_text(
            "# Concept 4\n\nExample code block:\n\n```javascript\nconsole.log('test');\n```\n",
            encoding="utf-8",
        )
        (self.project_root / "concept_5.md").write_text(
            "# Concept 5\n\nMore examples here.\n",
            encoding="utf-8",
        )

        self.auditor = ProjectAuditor(
            str(self.project_root),
            str(self.project_root / "checkpoints.json"),
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_knowledge_artifact_repo_detection(self):
        files = self.auditor.scan_directory(
            ignore_dirs=["__pycache__"],
            extensions=[".md", ".txt"],
            max_size=1024 * 1024,
        )
        audit = self.auditor.audit_project(files)

        # Project type should be based on the primary language (markdown)
        self.assertIn("markdown", audit["understanding"]["project_type"])
        # Project name should be from README
        self.assertEqual(audit["understanding"]["project_name"], "AI Skill Library")
        # Health score should be reasonable for knowledge repos (may be lower due to no tests and no entry point)
        print(f"Health score: {audit['health_score']}")
        print(f"Issues: {[issue['type'] for issue in audit['issues']]}")
        # Health score is 65 = 100 - 25 (no_tests) - 10 (no_entry_point)
        self.assertGreaterEqual(audit["health_score"], 60)


if __name__ == "__main__":
    unittest.main()
