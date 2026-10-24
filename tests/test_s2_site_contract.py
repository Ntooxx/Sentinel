"""Keep the landing page's illustrative examples honest.

The page shows a JSON contract, a source excerpt, and a few findings. Those are
examples, but they are presented as evidence, so they must not name fields,
rules, or lines that do not exist. This test fails when the page drifts from the
real report contract in ``report.py`` or from real rule ids in ``findings.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGE = (REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
FINDINGS_SRC = (REPO_ROOT / "src" / "sentinel2" / "findings.py").read_text(encoding="utf-8")

RULE_RE = re.compile(r"\b([A-Z]{2,4}\d{3})\b")
PLACEHOLDER_RULE_RE = re.compile(r"\b[A-Z]{2,4}-\d{3}\b")


def _real_rule_ids() -> set[str]:
    return set(RULE_RE.findall(FINDINGS_SRC))


def test_page_has_no_placeholder_rule_ids() -> None:
    found = PLACEHOLDER_RULE_RE.findall(PAGE)
    assert not found, f"hyphenated placeholder rule ids: {sorted(set(found))}"


def test_every_rule_shown_on_the_page_exists() -> None:
    shown = set(RULE_RE.findall(PAGE))
    unknown = shown - _real_rule_ids()
    assert not unknown, f"rule ids not defined in findings.py: {sorted(unknown)}"


def test_page_uses_the_real_json_contract_fields() -> None:
    assert '"schema_version"' in PAGE
    assert '"overall_score"' in PAGE
    assert '"rule_id"' in PAGE
    for stale in ('"quality"', '"version":', "sentinel.report.v2"):
        assert stale not in PAGE, f"stale contract fragment on the page: {stale}"


def test_evidence_excerpt_names_a_real_symbol() -> None:
    assert "_rank_findings" not in PAGE
    assert "_manifest_meta" in PAGE
    analyze = (REPO_ROOT / "src" / "sentinel2" / "analyze.py").read_text(encoding="utf-8")
    assert "def _manifest_meta(" in analyze
