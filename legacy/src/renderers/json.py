from __future__ import annotations

import json as json_module
from typing import Any, Dict


class JSONRenderer:
    """Renders Sentinel results as JSON for programmatic consumption."""

    def __init__(self, width: int = 60):
        self.width = width

    def render(self, result: Dict[str, Any]) -> str:
        return json_module.dumps(result, indent=2, sort_keys=True)
