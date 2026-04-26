import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) in sys.path:
    sys.path.remove(str(SRC))
sys.path.insert(0, str(SRC))

from knowledge import KnowledgeBase  # noqa: E402


class KnowledgeBaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.tempdir.name) / "knowledge.json"
        self.knowledge = KnowledgeBase(str(self.storage_path))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_summary_tracks_files_patterns_issues_and_decisions(self):
        self.knowledge.update_file_info(
            "src/app.py",
            {"line_count": 12, "extension": ".py", "size": 100},
            persist=False,
        )
        self.knowledge.add_pattern(
            {"name": "command_line_interface", "description": "CLI entry point"},
            persist=False,
        )
        self.knowledge.replace_issues(
            [{"type": "todo", "severity": "low", "message": "Contains TODO", "file": "src/app.py"}],
            persist=False,
        )
        self.knowledge.add_decision("Use JSON storage", "Portable and simple", persist=False)
        self.knowledge.set_last_scan(persist=False)
        self.knowledge.set_last_checkpoint("2026-01-01T00:00:00+00:00", persist=False)
        self.knowledge.save()

        reloaded = KnowledgeBase(str(self.storage_path))
        summary = reloaded.get_project_summary()

        self.assertEqual(summary["total_files"], 1)
        self.assertEqual(summary["total_lines"], 12)
        self.assertEqual(summary["patterns_found"], 1)
        self.assertEqual(summary["open_issues"], 1)
        self.assertEqual(summary["decisions_made"], 1)
        self.assertEqual(summary["last_checkpoint"], "2026-01-01T00:00:00+00:00")

    def test_export_context_includes_sections(self):
        self.knowledge.update_architecture({"entry_points": ["src/app.py"]}, persist=False)
        self.knowledge.update_dependencies({"python": {"manifests": ["requirements.txt"]}}, persist=False)
        self.knowledge.update_understanding(
            {
                "project_name": "example-app",
                "project_type": "python project with a CLI",
                "summary": "example-app is a python project with a CLI.",
                "main_components": [{"path": "src", "role": "application logic", "file_count": 1, "line_count": 12}],
                "important_files": [{"path": "src/app.py", "reason": "entry point"}],
            },
            persist=False,
        )
        self.knowledge.update_suggestions(
            [
                {
                    "title": "Trace runtime flow",
                    "priority": "medium",
                    "category": "debugging",
                    "reason": "Start from the CLI entry point",
                    "action": "Map the runtime path",
                    "suggested_prompt": "Trace the runtime path and explain the first safe change.",
                }
            ],
            persist=False,
        )
        self.knowledge.update_llm_readiness(
            {
                "recommended_budget": "small",
                "estimated_full_context_tokens": 1000,
                "estimated_compact_context_tokens": 200,
                "estimated_token_savings_percent": 80,
            },
            persist=False,
        )
        self.knowledge.replace_issues(
            [{"type": "no_tests", "severity": "high", "message": "No test files detected in project"}],
            persist=False,
        )
        self.knowledge.save()

        context = self.knowledge.export_context(budget="small")

        self.assertIn("# Project Knowledge Base", context)
        self.assertIn("## Project Understanding", context)
        self.assertIn("## Architecture", context)
        self.assertIn("## Dependencies", context)
        self.assertIn("## Recent Review Signals", context)
        self.assertIn("## Suggested Next Move", context)
        self.assertIn("## LLM Strategy", context)


if __name__ == "__main__":
    unittest.main()
