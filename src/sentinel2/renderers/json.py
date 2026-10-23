"""Stable JSON report renderer."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def render_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
