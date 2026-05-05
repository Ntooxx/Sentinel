import io
import json
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

from sentinel import main  # noqa: E402
from sentinel_mcp import SentinelMCPServer  # noqa: E402


class InterruptingTransport:
    def read_message(self):
        raise KeyboardInterrupt

    def write_message(self, payload):
        raise AssertionError(f"Unexpected payload: {payload}")


class SentinelMCPTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.workspace = self.base / "workspace"
        self.project = self.workspace / "app"
        (self.project / "tests").mkdir(parents=True)

        (self.project / "README.md").write_text("# Demo App\n", encoding="utf-8")
        (self.project / "pyproject.toml").write_text(
            '[project]\nname = "demo-app"\ndescription = "Small demo application"\n',
            encoding="utf-8",
        )
        (self.project / "app.py").write_text(
            '"""Demo app."""\n\n'
            "def main():\n"
            "    return 'ok'\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8",
        )
        (self.project / "tests" / "test_app.py").write_text(
            "def test_smoke():\n"
            "    assert True\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_mcp_server_lists_tools_and_returns_context(self):
        server = SentinelMCPServer(
            project_dir=str(self.project),
            workspace_root=str(self.workspace),
            budget="small",
            fast_mode=True,
        )

        init = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        self.assertEqual(init["result"]["serverInfo"]["name"], "sentinel-mcp")

        tools = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
        )
        tool_names = {tool["name"] for tool in tools["result"]["tools"]}
        self.assertIn("sentinel_context", tool_names)
        self.assertIn("sentinel_prompt", tool_names)

        context = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "sentinel_context", "arguments": {"budget": "small"}},
            }
        )
        text = context["result"]["content"][0]["text"]
        self.assertIn("SENTINEL CONTEXT PACK", text)
        self.assertIn("Project Knowledge Base", text)

        prompt = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "sentinel_prompt", "arguments": {"goal": "next"}},
            }
        )
        prompt_text = prompt["result"]["content"][0]["text"]
        self.assertIn("Goal:", prompt_text)
        self.assertIn("Compact project context:", prompt_text)

    def test_mcp_server_exits_cleanly_on_keyboard_interrupt(self):
        server = SentinelMCPServer(project_dir=str(self.project))
        server.serve(InterruptingTransport())

    def test_cli_kilo_setup_writes_project_files(self):
        sentinel_wrapper = self.workspace / "tools" / "sentinel"
        sentinel_wrapper.mkdir(parents=True)
        (sentinel_wrapper / "sentinel.py").write_text("# sentinel wrapper\n", encoding="utf-8")

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "kilo-setup",
                    str(self.workspace),
                    "--scan-root",
                    "app",
                    "--portable",
                ]
        )

        self.assertEqual(exit_code, 0)
        kilo_jsonc_path = self.workspace / ".kilo" / "kilo.jsonc"
        root_kilo_path = self.workspace / "kilo.json"
        modern_rule_path = self.workspace / ".kilo" / "rules" / "sentinel-first.md"
        agent_path = self.workspace / ".kilo" / "agents" / "sentinel-code.md"
        legacy_mcp_path = self.workspace / ".kilocode" / "mcp.json"
        legacy_rule_path = self.workspace / ".kilocode" / "rules" / "sentinel-first.md"
        self.assertTrue(kilo_jsonc_path.exists())
        self.assertTrue(root_kilo_path.exists())
        self.assertTrue(modern_rule_path.exists())
        self.assertTrue(agent_path.exists())
        self.assertTrue(legacy_mcp_path.exists())
        self.assertTrue(legacy_rule_path.exists())

        payload = json.loads(kilo_jsonc_path.read_text(encoding="utf-8"))
        server_cfg = payload["mcp"]["sentinel"]
        self.assertEqual(server_cfg["type"], "local")
        self.assertEqual(server_cfg["command"][0], "python")
        self.assertIn("tools/sentinel/sentinel.py", server_cfg["command"][1])
        self.assertIn("app", server_cfg["command"])
        self.assertEqual(payload["permission"]["sentinel_sentinel_prompt"], "allow")
        self.assertIn("AGENTS.md", payload["instructions"])
        self.assertIn("CONTEXT.md", payload["instructions"])
        self.assertIn(".kilo/rules/sentinel-first.md", payload["instructions"])
        self.assertIn(".kilo/rules/sentinel-file-bridge.md", payload["instructions"])
        self.assertIn("sentinel-refresh", payload["command"])

        root_payload = json.loads(root_kilo_path.read_text(encoding="utf-8"))
        self.assertIn("CONTEXT.md", root_payload["instructions"])
        self.assertIn("sentinel-refresh", root_payload["command"])
        self.assertIn("kilo-refresh", root_payload["command"]["sentinel-refresh"]["shell"])

        legacy_payload = json.loads(legacy_mcp_path.read_text(encoding="utf-8"))
        self.assertIn("sentinel", legacy_payload["mcpServers"])

        rule_text = modern_rule_path.read_text(encoding="utf-8")
        self.assertIn("tool_name: sentinel_prompt", rule_text)
        self.assertIn("sentinel_sentinel_prompt", rule_text)
        self.assertIn("approval keys only", rule_text)
        self.assertIn("scan `app` by default", rule_text)

        agent_text = agent_path.read_text(encoding="utf-8")
        self.assertIn("Sentinel Code Agent", agent_text)
        self.assertIn("server_name: sentinel", agent_text)
        self.assertIn("tool_name: sentinel_prompt", agent_text)
        self.assertIn("sentinel_sentinel_prompt", agent_text)

    def test_cli_kilo_refresh_writes_file_bridge(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "kilo-refresh",
                    str(self.workspace),
                    "--scan-root",
                    "app",
                    "--budget",
                    "small",
                    "--goal",
                    "next",
                    "--fast",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Sentinel Kilo bridge refreshed", output.getvalue())

        root_context_path = self.workspace / "CONTEXT.md"
        agents_path = self.workspace / "AGENTS.md"
        prompt_path = self.workspace / ".sentinel" / "kilo" / "prompt.md"
        context_path = self.workspace / ".sentinel" / "kilo" / "context.md"
        overview_path = self.workspace / ".sentinel" / "kilo" / "overview.md"
        focus_path = self.workspace / ".sentinel" / "kilo" / "focus-files.txt"
        status_path = self.workspace / ".sentinel" / "kilo" / "status.json"
        rule_path = self.workspace / ".kilo" / "rules" / "sentinel-file-bridge.md"
        ignore_path = self.workspace / ".kilocodeignore"

        for path in [
            root_context_path,
            agents_path,
            prompt_path,
            context_path,
            overview_path,
            focus_path,
            status_path,
            rule_path,
            ignore_path,
        ]:
            self.assertTrue(path.exists(), f"Expected bridge file to exist: {path}")

        root_context = root_context_path.read_text(encoding="utf-8")
        self.assertIn("# Sentinel Context", root_context)
        self.assertIn("## Compact Context", root_context)
        self.assertIn("## Focus Files", root_context)
        self.assertIn("Paths are relative to the workspace root.", root_context)

        agents_text = agents_path.read_text(encoding="utf-8")
        self.assertIn("Primary path, no MCP required", agents_text)
        self.assertIn("project-sentinel kilo-refresh", agents_text)

        status = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["budget"], "small")
        self.assertEqual(Path(status["scan_root"]), self.project.resolve())
        self.assertTrue(status["context_fresh"])
        self.assertEqual(status["invalid_focus_files"], [])
        self.assertTrue(all(path.startswith("app/") for path in status["focus_files"]))
        self.assertIn("status", status["paths"])


if __name__ == "__main__":
    unittest.main()
