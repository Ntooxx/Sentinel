from __future__ import annotations

from pathlib import Path

from utils import ensure_parent_dir

from renderers.terminal import TerminalRenderer
from renderers.markdown import MarkdownRenderer
from renderers.html import HTMLRenderer
from renderers.json import JSONRenderer
from renderers.compact import CompactRenderer
from renderers.ask import AskRenderer
from renderers.status import StatusRenderer


class ReportGenerator:
    """Facade that delegates to format-specific renderers."""

    def __init__(self, width: int = 60):
        self.terminal = TerminalRenderer(width)
        self.markdown = MarkdownRenderer(width)
        self.html = HTMLRenderer(width)
        self.json_renderer = JSONRenderer(width)
        self.compact = CompactRenderer(width)
        self.ask = AskRenderer(width)
        self.status = StatusRenderer(width)

    def render_terminal(self, *args, **kwargs):
        return self.terminal.render(*args, **kwargs)

    def render_markdown(self, *args, **kwargs):
        return self.markdown.render(*args, **kwargs)

    def render_html(self, *args, **kwargs):
        return self.html.render(*args, **kwargs)

    def render_json(self, *args, **kwargs):
        return self.json_renderer.render(*args, **kwargs)

    def render_compact(self, *args, **kwargs):
        return self.compact.render(*args, **kwargs)

    def render_ask_answer(self, *args, **kwargs):
        return self.ask.render(*args, **kwargs)

    def render_status(self, *args, **kwargs):
        return self.status.render(*args, **kwargs)

    def render_brief(self, *args, **kwargs):
        return self.terminal.render_brief(*args, **kwargs)

    def render_overview(self, *args, **kwargs):
        return self.markdown.render_overview(*args, **kwargs)

    def render_context_pack(self, *args, **kwargs):
        return self.compact.render_context_pack(*args, **kwargs)

    def render_prompt_pack(self, *args, **kwargs):
        return self.compact.render_prompt_pack(*args, **kwargs)

    def save_markdown(self, text, path):
        ensure_parent_dir(path).write_text(text, encoding="utf-8")
