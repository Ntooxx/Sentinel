"""Keep the landing page's illustrative examples honest.

The page shows findings and a source excerpt. Those are examples, but they are
presented as evidence, so they must not name fields, rules, or symbols that do
not exist. This test fails when the page drifts from the real report schema in
``docs/report.schema.json`` or from real rule ids in ``findings.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGE = (REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
FINDINGS_SRC = (REPO_ROOT / "src" / "sentinel2" / "findings.py").read_text(encoding="utf-8")
SCHEMA = json.loads((REPO_ROOT / "docs" / "report.schema.json").read_text(encoding="utf-8"))

RULE_RE = re.compile(r"\b([A-Z]{2,4}\d{3})\b")
PLACEHOLDER_RULE_RE = re.compile(r"\b[A-Z]{2,4}-\d{3}\b")
JSON_BLOCK_RE = re.compile(r'<pre class="json-code">(.*?)</pre>', re.S)
KEY_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')


def _schema_keys(node: object) -> set[str]:
    """Every property name the published schema defines, at any depth."""
    keys: set[str] = set()
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            keys.update(properties)
            for value in properties.values():
                keys |= _schema_keys(value)
        for value in node.values():
            keys |= _schema_keys(value)
    elif isinstance(node, list):
        for item in node:
            keys |= _schema_keys(item)
    return keys


def test_page_has_no_placeholder_rule_ids() -> None:
    found = PLACEHOLDER_RULE_RE.findall(PAGE)
    assert not found, f"hyphenated placeholder rule ids: {sorted(set(found))}"


def test_every_rule_shown_on_the_page_exists() -> None:
    unknown = set(RULE_RE.findall(PAGE)) - set(RULE_RE.findall(FINDINGS_SRC))
    assert not unknown, f"rule ids not defined in findings.py: {sorted(unknown)}"


def test_any_json_example_uses_the_published_schema() -> None:
    allowed = _schema_keys(SCHEMA)
    assert allowed, "docs/report.schema.json defines no properties"
    for block in JSON_BLOCK_RE.findall(PAGE):
        unknown = set(KEY_RE.findall(block)) - allowed
        assert not unknown, f"JSON example keys not in report.schema.json: {sorted(unknown)}"


def test_evidence_excerpt_names_a_real_symbol() -> None:
    assert "_rank_findings" not in PAGE
    assert "_manifest_meta" in PAGE
    analyze = (REPO_ROOT / "src" / "sentinel2" / "analyze.py").read_text(encoding="utf-8")
    assert "def _manifest_meta(" in analyze
