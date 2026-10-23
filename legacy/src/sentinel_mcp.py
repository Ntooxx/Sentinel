from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "sentinel-mcp"
SERVER_VERSION = "1.1.0"


class MCPIO:
    """Tiny stdio transport for MCP framed messages."""

    def __init__(self, reader: Any = None, writer: Any = None):
        self.reader = reader or sys.stdin.buffer
        self.writer = writer or sys.stdout.buffer

    def read_message(self) -> Dict[str, Any] | None:
        headers: dict[str, str] = {}
        while True:
            line = self.reader.readline()
            if not line:
                return None
            if line in {b"\r\n", b"\n"}:
                break
            decoded = line.decode("utf-8").strip()
            if ":" not in decoded:
                continue
            key, value = decoded.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        length = int(headers.get("content-length", "0") or "0")
        if length <= 0:
            return None

        payload = self.reader.read(length)
        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))

    def write_message(self, payload: Dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
        self.writer.write(header)
        self.writer.write(encoded)
        self.writer.flush()


class SentinelMCPServer:
    """Expose Sentinel as a tiny MCP tool surface for Kilo and similar agents."""

    def __init__(
        self,
        project_dir: str = ".",
        workspace_root: str | None = None,
        config_path: str | None = None,
        budget: str = "small",
        fast_mode: bool = True,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else self.project_dir
        self.config_path = config_path
        self.budget = budget
        self.fast_mode = fast_mode

    def serve(self, io: MCPIO | None = None) -> None:
        transport = io or MCPIO()
        try:
            while True:
                message = transport.read_message()
                if message is None:
                    break
                response = self.handle(message)
                if response is not None:
                    transport.write_message(response)
                if message.get("method") == "exit":
                    break
        except KeyboardInterrupt:
            return

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params", {}) or {}

        if method == "notifications/initialized":
            return None

        if method == "initialize":
            return self._result(
                request_id,
                {
                    "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                    "capabilities": {
                        "tools": {
                            "listChanged": False,
                        }
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                },
            )

        if method == "ping":
            return self._result(request_id, {})

        if method == "shutdown":
            return self._result(request_id, {})

        if method == "exit":
            return None

        if method == "tools/list":
            return self._result(request_id, {"tools": self.list_tools()})

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {}) or {}
            return self._result(request_id, self.call_tool(name, arguments))

        if request_id is None:
            return None
        return self._error(request_id, -32601, f"Method not found: {method}")

    def list_tools(self) -> list[Dict[str, Any]]:
        return [
            {
                "name": "sentinel_context",
                "description": (
                    "Return a compact, low-token project context pack. "
                    "Use this before broad repo reads."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_dir": {
                            "type": "string",
                            "description": "Optional subdirectory relative to the workspace root.",
                        },
                        "budget": {
                            "type": "string",
                            "enum": ["tiny", "small", "medium", "large"],
                            "description": "How much compact context to include.",
                        },
                        "fast": {
                            "type": "boolean",
                            "description": "Use a faster scan mode.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "sentinel_overview",
                "description": (
                    "Explain the project structure, hotspots, important files, and token strategy."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_dir": {
                            "type": "string",
                            "description": "Optional subdirectory relative to the workspace root.",
                        },
                        "fast": {
                            "type": "boolean",
                            "description": "Use a faster scan mode.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "sentinel_prompt",
                "description": (
                    "Generate a focused next-step prompt grounded in compact Sentinel context."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_dir": {
                            "type": "string",
                            "description": "Optional subdirectory relative to the workspace root.",
                        },
                        "goal": {
                            "type": "string",
                            "enum": ["next", "debug", "review", "plan", "document", "test"],
                            "description": "What kind of prompt to generate.",
                        },
                        "budget": {
                            "type": "string",
                            "enum": ["tiny", "small", "medium", "large"],
                            "description": "How much compact context to include.",
                        },
                        "suggestion_number": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Which Sentinel suggestion to anchor the prompt to.",
                        },
                        "fast": {
                            "type": "boolean",
                            "description": "Use a faster scan mode.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        ]

    def call_tool(self, name: str | None, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if name == "sentinel_context":
                return self._tool_context(arguments)
            if name == "sentinel_overview":
                return self._tool_overview(arguments)
            if name == "sentinel_prompt":
                return self._tool_prompt(arguments)
            return self._tool_error(f"Unknown Sentinel tool: {name}")
        except Exception as exc:  # pragma: no cover - defensive MCP surface
            return self._tool_error(f"Sentinel MCP failed: {exc}")

    def _tool_context(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        agent = self._make_agent(arguments)
        try:
            agent.scan_once(
                print_report=False,
                fast_mode=self._fast(arguments),
                include_suggestions=True,
                create_checkpoint=True,
            )
            pack = agent.build_context_pack(budget=str(arguments.get("budget", self.budget)))
            text = agent.reporter.render_context_pack(pack)
            return self._tool_result(text, pack)
        finally:
            agent.close()

    def _tool_overview(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        agent = self._make_agent(arguments)
        try:
            result = agent.scan_once(
                print_report=False,
                fast_mode=self._fast(arguments),
                include_suggestions=True,
                create_checkpoint=True,
            )
            text = agent.reporter.render_overview(result)
            return self._tool_result(text, result)
        finally:
            agent.close()

    def _tool_prompt(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        agent = self._make_agent(arguments)
        try:
            result = agent.scan_once(
                print_report=False,
                fast_mode=self._fast(arguments),
                include_suggestions=True,
                create_checkpoint=True,
            )
            pack = agent.build_prompt_pack(
                result=result,
                goal=str(arguments.get("goal", "next")),
                budget=str(arguments.get("budget", self.budget)),
                suggestion_number=int(arguments.get("suggestion_number", 1) or 1),
            )
            text = agent.reporter.render_prompt_pack(pack)
            return self._tool_result(text, pack)
        finally:
            agent.close()

    def _make_agent(self, arguments: Dict[str, Any]) -> Any:
        from sentinel import SentinelAgent

        project_dir = self._resolve_project_dir(arguments.get("project_dir"))
        agent = SentinelAgent(str(project_dir), self.config_path)
        logging.getLogger().setLevel(logging.ERROR)
        for handler in logging.getLogger().handlers:
            handler.setLevel(logging.ERROR)
        agent.log.setLevel(logging.ERROR)
        return agent

    def _resolve_project_dir(self, raw: Any) -> Path:
        if raw is None or str(raw).strip() == "":
            return self.project_dir
        candidate = Path(str(raw)).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.workspace_root / candidate).resolve()

    def _fast(self, arguments: Dict[str, Any]) -> bool:
        if "fast" in arguments:
            return bool(arguments["fast"])
        return self.fast_mode

    def _tool_result(self, text: str, structured: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "content": [
                {
                    "type": "text",
                    "text": text,
                }
            ],
            "structuredContent": structured,
            "isError": False,
        }

    def _tool_error(self, message: str) -> Dict[str, Any]:
        return {
            "content": [
                {
                    "type": "text",
                    "text": message,
                }
            ],
            "isError": True,
        }

    def _result(self, request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }

    def _error(self, request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }
