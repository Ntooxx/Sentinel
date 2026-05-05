import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) in sys.path:
    sys.path.remove(str(SRC))
sys.path.insert(0, str(SRC))

from sentinel import SentinelAgent, _dashboard_html, _run_dashboard_action, main  # noqa: E402
from suggester import Suggester  # noqa: E402
from utils import DEFAULT_CONFIG  # noqa: E402


class SentinelAgentTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.project_root = self.base / "project"
        self.runtime_root = self.base / "runtime"
        self.agent = None

        (self.project_root / "tests").mkdir(parents=True)
        self.runtime_root.mkdir(parents=True)

        (self.project_root / "README.md").write_text("# Example\n", encoding="utf-8")
        (self.project_root / "requirements.txt").write_text("requests\n", encoding="utf-8")
        (self.project_root / "app.py").write_text(
            "def main():\n"
            "    return 'ok'\n\n"
            "def helper(value):\n"
            "    return main() + value\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8",
        )
        (self.project_root / "tests" / "test_app.py").write_text(
            "def test_main():\n"
            "    assert True\n",
            encoding="utf-8",
        )

        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                "scan_interval_seconds": 1,
                "knowledge_base_path": str(self.runtime_root / "knowledge_base.json"),
                "checkpoints_path": str(self.runtime_root / "checkpoints.json"),
                "reports_path": str(self.runtime_root / "reports"),
                "log_file": str(self.runtime_root / "sentinel.log"),
                "audit_rules_path": str(ROOT / "config" / "audit_rules.json"),
                "patterns_path": str(ROOT / "config" / "patterns.json"),
            }
        )
        self.config_path = self.base / "config.json"
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def tearDown(self):
        if self.agent is not None:
            self.agent.close()
        self.tempdir.cleanup()

    def test_scan_once_updates_runtime_files_and_reports_health(self):
        self.agent = SentinelAgent(str(self.project_root), str(self.config_path))
        result = self.agent.scan_once(print_report=False, fast_mode=True)

        self.assertGreaterEqual(result["files_scanned"], 4)
        self.assertGreaterEqual(result["audit"]["health_score"], 70)
        self.assertTrue(Path(self.agent.knowledge_path).exists())
        self.assertTrue(Path(self.agent.checkpoint_path).exists())
        self.assertTrue(result["performance"]["fast_mode"])

        issue_types = {issue["type"] for issue in result["audit"]["issues"]}
        self.assertNotIn("no_tests", issue_types)
        self.assertNotIn("no_readme", issue_types)
        self.assertIn("risk_scores", result["audit"])
        self.assertIn("budget_alerts", result["performance"])
        self.assertGreaterEqual(result["project_summary"]["scan_history_count"], 1)

        top = result["suggestions"][0]
        self.assertIn("confidence", top)
        self.assertIn("impact", top)
        self.assertIn("effort", top)

    def test_non_source_config_changes_do_not_create_test_update_suggestions(self):
        suggester = Suggester()
        audit = {
            "issues": [],
            "metrics": {"open_todos": 0},
            "structure": {"has_tests": True, "test_files": ["tests/test_app.py"], "entry_points": ["app.py"]},
            "understanding": {"hotspots": []},
        }
        diff = {"modified_files": [".env"], "summary": "~1 modified"}
        suggestions = suggester.generate_suggestions(audit, diff, {"files": {".env": {}}})

        self.assertNotIn("Update tests for .env", {suggestion["title"] for suggestion in suggestions})

    def test_full_report_is_generated_and_saved(self):
        self.agent = SentinelAgent(str(self.project_root), str(self.config_path))
        report_text = self.agent.get_full_report()

        self.assertIn("# Sentinel Report", report_text)
        self.assertIn("- Health Score: 95%", report_text)
        self.assertIn("- Risk Summary:", report_text)
        self.assertIn("## Knowledge Context", report_text)

        saved = self.agent.save_full_report()
        self.assertTrue(Path(saved["primary_path"]).exists())
        self.assertTrue(Path(saved["archive_path"]).exists())

        html = self.agent.get_html_report(fast_mode=True)
        self.assertIn("<!doctype html>", html)
        self.assertIn("Sentinel Report", html)

        html_saved = self.agent.save_html_report(fast_mode=True)
        self.assertTrue(Path(html_saved["primary_path"]).exists())
        self.assertTrue(Path(html_saved["archive_path"]).exists())

    def test_cli_scan_and_status_commands_work(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "scan",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--fast",
                    "--compact",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("mode=fast", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "status",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--format",
                    "json",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn('"project_dir"', output.getvalue())

    def test_cli_brief_command_is_small_and_uses_top_suggestion(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "brief",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--fast",
                    "--top",
                    "1",
                ]
            )

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("health=", rendered)
        self.assertIn("next:", rendered)
        self.assertIn("mode=fast", rendered)

    def test_cli_overview_context_and_prompt_commands_work(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "overview",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--fast",
                ]
            )

        self.assertEqual(exit_code, 0)
        overview = output.getvalue()
        self.assertIn("SENTINEL OVERVIEW", overview)
        self.assertIn("Main Components:", overview)
        self.assertIn("Token Strategy:", overview)

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "context",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--fast",
                    "--budget",
                    "small",
                ]
            )

        self.assertEqual(exit_code, 0)
        context = output.getvalue()
        self.assertIn("SENTINEL CONTEXT PACK", context)
        self.assertIn("Estimated Savings:", context)

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "prompt",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--fast",
                    "--goal",
                    "next",
                ]
            )

        self.assertEqual(exit_code, 0)
        prompt = output.getvalue()
        self.assertIn("Goal:", prompt)
        self.assertIn("Compact project context:", prompt)

    def test_cli_retrieve_graph_verify_and_doctor_commands_work(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "retrieve",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--query",
                    "helper main",
                    "--fast",
                ]
            )

        self.assertEqual(exit_code, 0)
        retrieved = output.getvalue()
        self.assertIn("SENTINEL RETRIEVAL", retrieved)
        self.assertIn("app.py", retrieved)
        self.assertIn("helper", retrieved)

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "graph",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                ]
            )

        self.assertEqual(exit_code, 0)
        graph = output.getvalue()
        self.assertIn("SENTINEL PYTHON GRAPH", graph)
        self.assertIn("app.main", graph)

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "verify",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--changed-file",
                    "app.py",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        verify = output.getvalue()
        self.assertIn("SENTINEL VERIFY", verify)
        self.assertIn("pytest", verify)

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "doctor",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("SENTINEL DOCTOR", output.getvalue())
        self.assertIn("mcp_surface", output.getvalue())

    def test_cli_ask_and_html_report_commands_work(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "ask",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--question",
                    "where is helper main",
                    "--fast",
                ]
            )

        self.assertEqual(exit_code, 0)
        answer = output.getvalue()
        self.assertIn("SENTINEL ASK", answer)
        self.assertIn("app.py", answer)

        html_path = self.base / "report.html"
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "report",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--format",
                    "html",
                    "--output",
                    str(html_path),
                    "--fast",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(html_path.exists())
        self.assertIn("<!doctype html>", html_path.read_text(encoding="utf-8"))

    def test_dashboard_gui_and_action_runner_work(self):
        self.agent = SentinelAgent(str(self.project_root), str(self.config_path))
        html = _dashboard_html(("127.0.0.1", 8765), "/")

        self.assertIn("Command Center", html)
        self.assertIn("run('ask')", html)
        self.assertIn("run('report_html')", html)
        self.assertIn("run('insights')", html)
        self.assertIn("run('bundle')", html)
        self.assertIn("/api/run", html)

        ask = _run_dashboard_action(
            self.agent,
            {
                "action": "ask",
                "question": "where is helper main",
                "fast": True,
            },
        )
        self.assertTrue(ask["ok"])
        self.assertIn("SENTINEL ASK", ask["text"])
        self.assertIn("app.py", ask["text"])

        report_path = self.base / "gui-report.html"
        report = _run_dashboard_action(
            self.agent,
            {
                "action": "report_html",
                "output_path": str(report_path),
                "fast": True,
            },
        )
        self.assertTrue(report["ok"])
        self.assertTrue(report_path.exists())
        self.assertIn(str(report_path), report["artifacts"])

        insights = _run_dashboard_action(
            self.agent,
            {
                "action": "insights",
                "question": "helper main",
                "fast": True,
            },
        )
        self.assertTrue(insights["ok"])
        self.assertIn("SENTINEL INSIGHTS", insights["text"])
        self.assertIn("drilldowns", insights["data"])

    def test_cli_insights_alerts_ledger_bundle_and_speed_plan_work(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "insights",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--query",
                    "helper main",
                    "--fast",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("SENTINEL INSIGHTS", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "alerts",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--fast",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("SENTINEL WATCH ALERTS", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "ledger",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("SENTINEL DECISION LEDGER", output.getvalue())

        bundle_dir = self.base / "bundle"
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "bundle",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--output-dir",
                    str(bundle_dir),
                    "--fast",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue((bundle_dir / "index.html").exists())
        self.assertTrue((bundle_dir / "analysis.json").exists())
        self.assertIn("SENTINEL STATIC BUNDLE", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "speed-plan",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--fast",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("SENTINEL SCAN SPEED PLAN", output.getvalue())

    def test_scan_cache_reuses_unchanged_file_metadata(self):
        self.agent = SentinelAgent(str(self.project_root), str(self.config_path))
        first = self.agent.scan_once(print_report=False, fast_mode=False)
        second = self.agent.scan_once(print_report=False, fast_mode=False, create_checkpoint=False)

        self.assertGreater(first["performance"]["cache"]["misses"], 0)
        self.assertGreater(second["performance"]["cache"]["hits"], 0)
        self.assertEqual(second["performance"]["cache"]["misses"], 0)

    @unittest.skipIf(shutil.which("git") is None, "git is required for analyze-url")
    def test_cli_analyze_url_command_clones_local_git_source(self):
        subprocess.run(["git", "init"], cwd=self.project_root, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "user.email", "sentinel@example.com"], cwd=self.project_root, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "user.name", "Sentinel Test"], cwd=self.project_root, capture_output=True, text=True, check=True)
        subprocess.run(["git", "add", "."], cwd=self.project_root, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.project_root, capture_output=True, text=True, check=True)

        output_dir = self.base / "url-report"
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "analyze-url",
                    str(self.project_root),
                    "--output-dir",
                    str(output_dir),
                    "--fast",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("SENTINEL URL ANALYSIS", output.getvalue())
        self.assertTrue((output_dir / "SENTINEL_REPORT.md").exists())
        self.assertTrue((output_dir / "SENTINEL_REPORT.html").exists())
        self.assertTrue((output_dir / "CONTEXT.md").exists())

    def test_cli_memory_savings_features_and_adapters_work(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "memory",
                    "record",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--goal",
                    "implemented retrieve",
                    "--changed-file",
                    "src/sentinel.py",
                    "--test",
                    "python -m pytest tests",
                    "--risk",
                    "none",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("implemented retrieve", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "memory",
                    "list",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("src/sentinel.py", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "savings",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("SENTINEL SAVINGS", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["features"])

        self.assertEqual(exit_code, 0)
        self.assertIn("SENTINEL COMMAND CENTER", output.getvalue())
        self.assertIn("retrieve", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "adapters",
                    str(self.project_root),
                    "--config",
                    str(self.config_path),
                    "--write",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("SENTINEL ADAPTERS", output.getvalue())
        self.assertTrue((self.project_root / ".sentinel" / "adapters" / "codex.md").exists())

    def test_cli_product_hardening_commands_work(self):
        commands = [
            (["autofix", str(self.project_root), "--config", str(self.config_path)], "SENTINEL AUTOFIX"),
            (["pr", str(self.project_root), "--config", str(self.config_path)], "SENTINEL PR SUMMARY"),
            (["timeline", str(self.project_root), "--config", str(self.config_path)], "SENTINEL MEMORY TIMELINE"),
            (["mcp-health", str(self.project_root), "--config", str(self.config_path)], "SENTINEL MCP HEALTH"),
            (["coverage", str(self.project_root), "--config", str(self.config_path)], "SENTINEL COVERAGE"),
            (["cleanup-reports", str(self.project_root), "--config", str(self.config_path)], "SENTINEL REPORT CLEANUP"),
            (["release-check", str(self.project_root), "--config", str(self.config_path)], "SENTINEL RELEASE CHECK"),
        ]
        for argv, expected in commands:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(argv)
            self.assertEqual(exit_code, 0, argv)
            self.assertIn(expected, output.getvalue(), argv)


if __name__ == "__main__":
    unittest.main()
