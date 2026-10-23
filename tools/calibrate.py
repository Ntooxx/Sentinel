#!/usr/bin/env python3
"""Calibrate the Sentinel quality rubric against a corpus of real repositories.

This is the evidence behind ``rules.calibrated``. The blueprint (sentinel_2.md
section 4.2) says the overall score stays ``provisional`` until the rubric has
been calibrated on at least 10 repositories. This tool makes that check
reproducible instead of a claim:

* every corpus entry carries an expected grade written from manual review;
* the harness reports how far each observed grade sits from that expectation;
* ``--check`` fails unless the acceptance criteria in the corpus file hold.

Expectations are human judgments recorded up front. If Sentinel disagrees, the
rubric gets adjusted - the expectations do not get edited to match the output.

This is a developer tool, not a public command.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sentinel2.config import load_rules  # noqa: E402
from sentinel2.utils import (  # noqa: E402
    BAND_ORDER,
    band_for,
    band_rank,
    bands_from_rules,
    grade_score,
)

GRADE_ORDER = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F"]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _band_of_grade(grade: str, bands: tuple[tuple[int, str], ...]) -> str | None:
    floor = grade_score(grade)
    if floor is None:
        return None
    return band_for(floor, bands)


def _grade_distance(expected: str, observed: str) -> int | None:
    if expected not in GRADE_ORDER or observed not in GRADE_ORDER:
        return None
    return abs(GRADE_ORDER.index(expected) - GRADE_ORDER.index(observed))


def _band_distance(expected: str | None, observed: str | None) -> int | None:
    if expected not in BAND_ORDER or observed not in BAND_ORDER:
        return None
    return abs(BAND_ORDER.index(str(expected)) - BAND_ORDER.index(str(observed)))


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2.0 + 1.0
        for offset in range(position, end + 1):
            ranks[order[offset]] = average
        position = end + 1
    return ranks


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    left_rank = _rank(left)
    right_rank = _rank(right)
    mean_left = statistics.fmean(left_rank)
    mean_right = statistics.fmean(right_rank)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left_rank, right_rank, strict=True))
    left_sq = sum((a - mean_left) ** 2 for a in left_rank)
    right_sq = sum((b - mean_right) ** 2 for b in right_rank)
    if left_sq == 0 or right_sq == 0:
        return None
    return numerator / ((left_sq * right_sq) ** 0.5)


def _tier_band_ranges(rows: list[dict[str, Any]]) -> dict[int, tuple[int, int]]:
    """Lowest and highest band rank observed in each expectation tier."""

    grouped: dict[int, list[int]] = {}
    for row in rows:
        tier = row.get("tier")
        rank = band_rank(str(row.get("band") or ""))
        if not isinstance(tier, int) or tier <= 0 or rank is None:
            continue
        grouped.setdefault(tier, []).append(rank)
    return {tier: (min(values), max(values)) for tier, values in sorted(grouped.items()) if values}


def _tier_inversions(ranges: dict[int, tuple[int, int]]) -> list[str]:
    """Flag a worse tier reaching a band that a better tier does not.

    Bands deliberately allow ties, so a lower tier sharing the band of a higher
    tier is not an inversion. The gate only fires when clearly weaker code is
    placed above clearly stronger code.
    """

    tiers = list(ranges)
    inversions: list[str] = []
    for index, better in enumerate(tiers):
        for worse in tiers[index + 1 :]:
            if ranges[better][0] < ranges[worse][1]:
                inversions.append(
                    f"tier {better} reaches {BAND_ORDER[ranges[better][0]]} while "
                    f"tier {worse} reaches {BAND_ORDER[ranges[worse][1]]}"
                )
    return inversions


def _scan(repo: Path, out: Path) -> dict[str, Any]:
    command = [sys.executable, "-m", "sentinel2", "scan", str(repo), "--format", "json", "--out", str(out), "--quiet"]
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        check=False,
    )
    if result.returncode not in (0, 3):
        raise SystemExit(f"scan failed for {repo} ({result.returncode}): {result.stderr.strip()}")
    return json.loads(out.read_text(encoding="utf-8"))


def _clone(entry: dict[str, Any], corpus: Path) -> Path | None:
    url = entry.get("url")
    name = entry["name"]
    target = corpus / name
    if target.is_dir():
        return target
    if not url:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet", url, str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return target if result.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", help="Directory holding the cloned repositories")
    parser.add_argument("--expect", default=str(ROOT / "config" / "calibration_corpus.json"), help="Expectations file")
    parser.add_argument("--clone", action="store_true", help="Clone missing corpus repositories first")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--check", action="store_true", help="Exit 1 unless the acceptance criteria hold")
    args = parser.parse_args(argv)

    corpus_root = Path(args.corpus or os.environ.get("SENTINEL_CALIBRATION_CORPUS") or "").expanduser()
    if not str(corpus_root):
        raise SystemExit("pass --corpus or set SENTINEL_CALIBRATION_CORPUS")

    spec = json.loads(Path(args.expect).read_text(encoding="utf-8"))
    acceptance = spec.get("acceptance", {})
    entries: list[dict[str, Any]] = spec.get("repos", [])
    bands = bands_from_rules(load_rules())

    temp_dir = Path(tempfile.mkdtemp(prefix="sentinel-calibrate-"))
    rows: list[dict[str, Any]] = []
    try:
        for entry in entries:
            name = entry["name"]
            repo = corpus_root / name
            if not repo.is_dir() and args.clone:
                repo = _clone(entry, corpus_root) or repo
            if not repo.is_dir():
                rows.append({"name": name, "status": "missing"})
                continue
            report = _scan(repo, temp_dir / f"{name}.json")
            summary = report.get("summary", {})
            dimensions = {item["id"]: item for item in report.get("dimensions", [])}
            observed_band = str(summary.get("band") or "n/a")
            expected = str(entry.get("expected_grade") or "n/a").rstrip("?")
            expected_band = _band_of_grade(expected, bands)
            raw_scores = [int(item["score"]) for item in report.get("dimensions", []) if item.get("assessed") and item.get("score") is not None]
            assessed = [item["id"] for item in report.get("dimensions", []) if item.get("assessed")]
            docs_only = entry.get("docs_only", False)
            row = {
                "name": name,
                "status": "ok",
                "expected_grade": expected,
                "expected_band": expected_band,
                "band": observed_band,
                "overall_score": summary.get("overall_score"),
                "expected_score": _score_floor(expected),
                "raw_score": round(sum(raw_scores) / len(raw_scores), 1) if raw_scores else None,
                "band_distance": _band_distance(expected_band, observed_band),
                "confidence": summary.get("confidence"),
                "provisional": summary.get("provisional"),
                "analysed_lines": int(report.get("metrics", {}).get("source_lines", 0) or 0)
                + int(report.get("metrics", {}).get("test_lines", 0) or 0),
                "test_ratio": report.get("metrics", {}).get("test_to_source_file_ratio"),
                "assessed": assessed,
                "docs_only": docs_only,
                "dimensions": {key: dimensions[key]["grade"] for key in dimensions},
                "findings": len(report.get("findings", [])),
                "files": report.get("scan", {}).get("files_total"),
                "tier": entry.get("tier"),
                "rationale": entry.get("rationale", ""),
            }
            rows.append(row)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    scored = [
        row
        for row in rows
        if row.get("status") == "ok" and not row.get("docs_only") and row.get("overall_score") is not None
    ]
    distances = [row["band_distance"] for row in scored if row["band_distance"] is not None]
    expected_scores = [float(row["expected_score"]) for row in scored if row.get("expected_score") is not None]
    observed_scores = [float(row["overall_score"]) for row in scored if row.get("expected_score") is not None]
    rho = _spearman(expected_scores, observed_scores)
    low_confidence = [row["name"] for row in rows if row.get("confidence") == "low"]
    docs_rows = [row for row in rows if row.get("docs_only") and row.get("status") == "ok"]
    docs_bad = [
        row["name"]
        for row in docs_rows
        if row.get("band") not in {"n/a", None} and "maintainability" in (row.get("assessed") or [])
    ]

    tier_ranges = _tier_band_ranges(scored)
    tier_inversions = _tier_inversions(tier_ranges)

    failures: list[str] = []
    if len(scored) < int(acceptance.get("min_repos", 10)):
        failures.append(f"only {len(scored)} scorable repositories, need {acceptance.get('min_repos', 10)}")
    if not distances or max(distances) > int(acceptance.get("max_band_distance", 2)):
        worst = max(distances) if distances else 0
        failures.append(f"maximum band distance {worst} exceeds {acceptance.get('max_band_distance', 2)}")
    mean_distance = statistics.fmean(distances) if distances else None
    if mean_distance is None or mean_distance > float(acceptance.get("max_mean_band_distance", 1.0)):
        failures.append(f"mean band distance {mean_distance} exceeds {acceptance.get('max_mean_band_distance', 1.0)}")
    if acceptance.get("require_tier_separation", True) and tier_inversions:
        failures.append("quality tiers are not separated: " + "; ".join(tier_inversions))
    if len(low_confidence) > int(acceptance.get("max_low_confidence", 2)):
        failures.append(f"{len(low_confidence)} repositories at low confidence: {', '.join(low_confidence)}")
    if docs_bad:
        failures.append(f"docs-only repositories received source grades: {', '.join(docs_bad)}")

    payload = {
        "acceptance": acceptance,
        "repositories": rows,
        "summary": {
            "scorable": len(scored),
            "max_band_distance": max(distances) if distances else None,
            "mean_band_distance": round(statistics.fmean(distances), 2) if distances else None,
            "spearman": round(rho, 3) if rho is not None else None,
            "spearman_note": (
                "Informational only. The product claim is a coarse band, not a rank ordering: "
                "most calibration repositories are mature open source clustered in the same band, "
                "where the expected ordering is a matter of taste. Tier separation is the gate that "
                "matters: no clearly weaker tier may be placed in a band above a clearly stronger one. "
                "Ties are allowed."
            ),
            "tier_band_ranges": {
                str(tier): {"min": BAND_ORDER[low], "max": BAND_ORDER[high]}
                for tier, (low, high) in tier_ranges.items()
            },
            "tier_inversions": tier_inversions,
            "low_confidence": low_confidence,
            "docs_only_invented_source_grade": docs_bad,
            "passed": not failures,
        },
        "failures": failures,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Sentinel calibration - {corpus_root}")
        header = f"  {'repository':<22}{'expect':<14}{'band':<14}{'score':>6}{'dist':>5}{'lines':>8}{'ratio':>7}{'conf':>9}  files"
        print(header)
        for row in rows:
            if row.get("status") != "ok":
                print(f"  {row['name']:<22}{'MISSING':<14}")
                continue
            score = row["overall_score"] if row["overall_score"] is not None else "-"
            distance = row["band_distance"] if row["band_distance"] is not None else "-"
            ratio = row.get("test_ratio")
            print(
                f"  {row['name']:<22}{str(row.get('expected_band') or '-'):<14}{str(row.get('band') or '-'):<14}"
                f"{score:>6}{distance:>5}{row.get('analysed_lines', 0):>8}"
                f"{(f'{ratio:.2f}' if isinstance(ratio, (int, float)) else '-'):>7}"
                f"{str(row['confidence']):>9}  {row['files']}"
            )
        print()
        print(f"  scorable repositories : {len(scored)}")
        print(f"  max band distance     : {payload['summary']['max_band_distance']}")
        print(f"  mean band distance    : {payload['summary']['mean_band_distance']}")
        print(f"  tier band ranges      : {payload['summary']['tier_band_ranges']}")
        print(f"  rank correlation      : {payload['summary']['spearman']} (informational only)")
        print(f"  low confidence        : {', '.join(low_confidence) or 'none'}")
        if failures:
            print()
            for failure in failures:
                print(f"  FAIL: {failure}")
        else:
            print()
            print("  PASS: bands agree with manual review within acceptance criteria.")

    if args.check and failures:
        return 1
    return 0


def _score_floor(grade: str) -> int | None:
    floors = {
        "A+": 97, "A": 93, "A-": 90, "B+": 87, "B": 83, "B-": 80,
        "C+": 77, "C": 73, "C-": 70, "D": 60, "F": 0,
    }
    return floors.get(grade)


if __name__ == "__main__":
    raise SystemExit(main())
