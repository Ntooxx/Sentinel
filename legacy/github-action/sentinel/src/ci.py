#!/usr/bin/env python3
"""Sentinel CI — GitHub Action entrypoint.

Environment variables (set by action.yml):
  SENTINEL_ACTION_PATH    path to the action directory
  SENTINEL_SCAN_PATH      relative path within repo to scan
  SENTINEL_BUDGET         context budget (tiny/small/medium/large)
  SENTINEL_FAST_MODE      true/false
  SENTINEL_FAIL_ON        CI failure condition
  SENTINEL_COMMENT_TITLE  PR comment title
  SENTINEL_CHECKPOINT_DIR cached checkpoint directory
  SENTINEL_GITHUB_TOKEN   GitHub API token
  SENTINEL_BASE_REF       base branch ref (empty if not PR)
  SENTINEL_EVENT_NAME     github.event_name

  GITHUB_EVENT_PATH       path to GitHub event payload JSON
  GITHUB_REPOSITORY       owner/repo
  GITHUB_SHA              current commit SHA
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MARKER = "<!-- sentinel:analysis -->"

# ── helpers ──────────────────────────────────────────────────────────


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _path(key: str) -> Path:
    return Path(_env(key)).resolve()


def _github_api(url: str, token: str, method: str = "GET", body: Any = None) -> Any:
    """Call the GitHub REST API.  No external dependencies — stdlib only."""
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "sentinel-ci")
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
        req.add_header("Content-Length", str(len(data)))
    else:
        data = None
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        print(f"  GitHub API error {exc.code}: {body_text[:300]}", file=sys.stderr)
        return None


def _api_base() -> str:
    return "https://api.github.com"


def _owner_repo() -> tuple[str, str]:
    full = _env("GITHUB_REPOSITORY", "")
    if "/" in full:
        return full.split("/", 1)
    return "", ""


def _pr_number() -> int | None:
    path = _env("GITHUB_EVENT_PATH")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            event = json.load(f)
        return event.get("pull_request", {}).get("number")
    except (OSError, json.JSONDecodeError):
        return None


def _load_checkpoint(checkpoint_dir: Path) -> dict[str, Any] | None:
    path = checkpoint_dir / "base_scan.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _save_checkpoint(checkpoint_dir: Path, data: dict[str, Any]) -> None:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    path = checkpoint_dir / "base_scan.json"
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        print(f"  git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
    return result.stdout.strip()


def _current_branch(cwd: Path) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)


# ── Sentinel import ─────────────────────────────────────────────────


def _import_sentinel(action_path: Path) -> Any:
    """Import SentinelAgent from src/sentinel.py.

    action_path = .github/actions/sentinel/
    repo_root   = action_path.parent.parent.parent  = Sentinel/
    src_dir     = repo_root / src
    """
    repo_root = action_path.parent.parent.parent.resolve()
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from sentinel import SentinelAgent  # noqa: E402

    return SentinelAgent


# ── scan helpers ────────────────────────────────────────────────────


def scan_repo(
    scan_path: Path,
    fast_mode: bool,
    SentinelAgent: type,
    budget: str = "small",
) -> dict[str, Any]:
    agent = SentinelAgent(str(scan_path))
    result = agent.scan_once(
        print_report=False,
        fast_mode=fast_mode,
        include_suggestions=True,
        create_checkpoint=True,
    )

    pr_summary = agent.pr_summary(verify=False)
    result["_pr_summary"] = pr_summary
    return result


# ── delta computation ────────────────────────────────────────────────


def compute_delta(base: dict[str, Any], head: dict[str, Any]) -> dict[str, Any]:
    base_health = base.get("audit", {}).get("health_score", 0)
    head_health = head.get("audit", {}).get("health_score", 0)

    base_risks = {
        r["file"]: r
        for r in base.get("audit", {}).get("risk_scores", [])
    }
    head_risks = {
        r["file"]: r
        for r in head.get("audit", {}).get("risk_scores", [])
    }

    new_risk_files = [r for f, r in head_risks.items() if f not in base_risks and r.get("level") == "high"]
    worsened_risk_files = []
    for f, hr in head_risks.items():
        br = base_risks.get(f)
        if br and (hr.get("score", 0) or 0) > (br.get("score", 0) or 0) + 5:
            worsened_risk_files.append(hr)

    base_metrics = base.get("audit", {}).get("metrics", {})
    head_metrics = head.get("audit", {}).get("metrics", {})

    return {
        "health_before": base_health,
        "health_after": head_health,
        "health_delta": round(head_health - base_health, 1),
        "files_before": base_metrics.get("total_files", 0),
        "files_after": head_metrics.get("total_files", 0),
        "todos_before": base_metrics.get("total_todos", 0),
        "todos_after": head_metrics.get("total_todos", 0),
        "new_high_risk_files": new_risk_files,
        "worsened_risk_files": worsened_risk_files,
        "base_categories": base.get("audit", {}).get("health_score_data", {}).get("categories", {}),
        "head_categories": head.get("audit", {}).get("health_score_data", {}).get("categories", {}),
    }


# ── comment formatting ──────────────────────────────────────────────


def format_comment(
    delta: dict[str, Any],
    head: dict[str, Any],
    title: str,
    comment_title: str = "Sentinel Analysis",
) -> str:
    h_before = delta["health_before"]
    h_after = delta["health_after"]
    h_delta = delta["health_delta"]

    if h_delta > 0:
        delta_icon = "🟢 +"
        delta_label = f"+{h_delta}"
    elif h_delta < 0:
        delta_icon = "🔴 "
        delta_label = str(h_delta)
    else:
        delta_icon = "⚪ "
        delta_label = "0"

    health_color = "🟢" if h_after >= 80 else "🟡" if h_after >= 60 else "🔴"

    lines = [
        f"## {title}",
        "",
        f"**Health impact:** {h_before:.0f} → {h_after:.0f} ({delta_label}) {delta_icon}",
        "",
    ]

    categories = delta.get("head_categories", {})
    base_cats = delta.get("base_categories", {})
    if categories:
        lines.append("| Category | Before | After | ± |")
        lines.append("|---|---|---|---|")
        for cat_key in ["maintainability", "runtime", "test", "documentation", "security"]:
            cat = categories.get(cat_key, {})
            base_cat = base_cats.get(cat_key, {})
            if not cat:
                continue
            name = cat_key.replace("_", " ").title()
            before_val = base_cat.get("score", 100) if base_cat else 100
            after_val = cat.get("score", 100)
            diff = round(after_val - before_val, 1)
            diff_str = f"+{diff}" if diff > 0 else str(diff)
            lines.append(f"| {name} | {before_val:.0f} | {after_val:.0f} | {diff_str} |")
        lines.append("")

    new_risks = delta.get("new_high_risk_files", [])
    worsened = delta.get("worsened_risk_files", [])
    if new_risks or worsened:
        lines.append("<details>")
        lines.append(f"<summary><b>🔴 Risk changes ({len(new_risks)} new, {len(worsened)} worsened)</b></summary>")
        lines.append("")
        for r in new_risks:
            lines.append(f"- **{r['file']}** — new, risk score: {r.get('score', '?')}")
        for r in worsened:
            lines.append(f"- **{r['file']}** — worsened, risk score: {r.get('score', '?')}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    pr_summary = head.get("_pr_summary", {})
    changed = pr_summary.get("changed_files", [])
    test_cmds = pr_summary.get("suggested_tests", [])
    if changed:
        lines.append("<details>")
        lines.append(f"<summary><b>📁 Changed files ({len(changed)})</b></summary>")
        lines.append("")
        for f in changed[:20]:
            lines.append(f"- `{f}`")
        if len(changed) > 20:
            lines.append(f"- *...and {len(changed) - 20} more*")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if test_cmds:
        lines.append("<details>")
        lines.append(f"<summary><b>🧪 Suggested test commands</b></summary>")
        lines.append("")
        for cmd in test_cmds:
            lines.append("```")
            lines.append(cmd)
            lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    todos_delta = delta.get("todos_after", 0) - delta.get("todos_before", 0)
    if todos_delta > 0:
        lines.append(f"📝 **+{todos_delta} TODO/FIXME markers** added")
        lines.append("")

    metrics = head.get("audit", {}).get("metrics", {})
    lines.append("---")
    lines.append(
        f"_{comment_title} · "
        f"{metrics.get('total_files', 0)} files · "
        f"{metrics.get('total_lines', 0):,} lines · "
        f"{head.get('performance', {}).get('duration_seconds', 0):.1f}s_"
    )
    lines.append("")
    lines.append(MARKER)

    return "\n".join(lines)


# ── PR comment lifecycle ──────────────────────────────────────────


def _find_existing_comment(owner: str, repo: str, pr_num: int, token: str) -> int | None:
    url = f"{_api_base()}/repos/{owner}/{repo}/issues/{pr_num}/comments"
    data = _github_api(url, token)
    if not isinstance(data, list):
        return None
    for comment in data:
        body = comment.get("body", "")
        if MARKER in body:
            return comment["id"]
    return None


def upsert_comment(
    owner: str,
    repo: str,
    pr_num: int,
    token: str,
    body: str,
) -> bool:
    existing_id = _find_existing_comment(owner, repo, pr_num, token)
    if existing_id is not None:
        url = f"{_api_base()}/repos/{owner}/{repo}/issues/comments/{existing_id}"
        result = _github_api(url, token, method="PATCH", body={"body": body})
        action = "updated"
    else:
        url = f"{_api_base()}/repos/{owner}/{repo}/issues/{pr_num}/comments"
        result = _github_api(url, token, method="POST", body={"body": body})
        action = "created"
    if result is None:
        print(f"  Failed to {action} PR comment", file=sys.stderr)
        return False
    print(f"  PR comment {action}")
    return True


# ── fail-on evaluation ────────────────────────────────────────────


def should_fail(delta: dict[str, Any], fail_on: str) -> str | None:
    """Return a failure reason string, or None if CI should pass."""
    h_delta = delta["health_delta"]
    h_after = delta["health_after"]

    if fail_on == "drop-5" and h_delta <= -5:
        return f"Health dropped by {abs(h_delta):.0f} points (threshold: drop-5)"
    if fail_on == "drop-10" and h_delta <= -10:
        return f"Health dropped by {abs(h_delta):.0f} points (threshold: drop-10)"
    if fail_on == "below-70" and h_after < 70:
        return f"Health is {h_after:.0f}/100 (threshold: below-70)"
    if fail_on == "below-50" and h_after < 50:
        return f"Health is {h_after:.0f}/100 (threshold: below-50)"
    return None


# ── base branch scan ──────────────────────────────────────────────


def scan_base_branch(
    repo_root: Path,
    scan_path: Path,
    base_ref: str,
    fast_mode: bool,
    SentinelAgent: type,
    budget: str,
) -> dict[str, Any] | None:
    """Checkout base branch, scan the same path, return to original."""
    print(f"  Scanning base branch ({base_ref}) for health delta...")
    original = _current_branch(repo_root)
    try:
        _git(["fetch", "origin", base_ref], repo_root)
        _git(["checkout", base_ref], repo_root)
        result = scan_repo(scan_path, fast_mode, SentinelAgent, budget)
        return result
    except Exception as exc:
        print(f"  Base scan failed: {exc}", file=sys.stderr)
        return None
    finally:
        _git(["checkout", original], repo_root)


# ── main ──────────────────────────────────────────────────────────


def run_sentinel_ci() -> None:
    action_path = _path("SENTINEL_ACTION_PATH")
    scan_path_str = _env("SENTINEL_SCAN_PATH", ".")
    budget = _env("SENTINEL_BUDGET", "small")
    fast_mode = _env("SENTINEL_FAST_MODE", "true").lower() == "true"
    fail_on = _env("SENTINEL_FAIL_ON", "never")
    comment_title = _env("SENTINEL_COMMENT_TITLE", "Sentinel Analysis")
    checkpoint_dir = _path("SENTINEL_CHECKPOINT_DIR")
    github_token = _env("SENTINEL_GITHUB_TOKEN", "")
    base_ref = _env("SENTINEL_BASE_REF", "")
    event_name = _env("SENTINEL_EVENT_NAME", "")

    pr_num = _pr_number()
    owner, repo = _owner_repo()
    repo_root = action_path.parent.parent.parent.resolve()
    scan_path = (repo_root / scan_path_str).resolve()

    print("")
    label = f"Sentinel CI — {owner}/{repo}#{pr_num}" if pr_num else "Sentinel CI"
    print(f"  [SENTINEL] {label}")
    print(f"     Scan path: {scan_path}")
    print(f"     Budget: {budget}  Fast: {fast_mode}  Fail: {fail_on}")
    print("")

    SentinelAgent = _import_sentinel(action_path)

    head_result = scan_repo(scan_path, fast_mode, SentinelAgent, budget)
    head_health = head_result.get("audit", {}).get("health_score", 0)
    print(f"     Head health: {head_health:.0f}/100  [{head_result.get('files_scanned', 0)} files]")

    base_result = _load_checkpoint(checkpoint_dir)
    if base_result is None and base_ref and event_name == "pull_request":
        base_result = scan_base_branch(
            repo_root, scan_path, base_ref, fast_mode, SentinelAgent, budget
        )
        if base_result:
            _save_checkpoint(checkpoint_dir, base_result)
            base_health = base_result.get("audit", {}).get("health_score", 0)
            print(f"     Base health: {base_health:.0f}/100  [{base_result.get('files_scanned', 0)} files]")

    delta = compute_delta(base_result or head_result, head_result)

    if not base_result:
        print(f"\n     Health: {delta['health_after']:.0f}/100 (no baseline — first scan)")
        print(f"     Files: {delta['files_after']}  TODOs: {delta['todos_after']}")
    else:
        print(f"\n     Health: {delta['health_after']:.0f}/100  ({delta['health_delta']:+.1f} vs base)")
        print(f"     New high-risk files: {len(delta['new_high_risk_files'])}")
        print(f"     Files: {delta['files_after']}  TODOs: {delta['todos_after']}")
    print("")

    if pr_num and github_token and owner and repo:
        comment_body = format_comment(delta, head_result, comment_title)
        ok = upsert_comment(owner, repo, pr_num, github_token, comment_body)
        if not ok:
            print("  [WARN] Could not post PR comment (forked PR? missing permissions?)")
            print("     Analysis still complete.")
    elif pr_num:
        print("  [WARN] No GitHub token available — skipping PR comment")
    else:
        print("  Not a PR event — skipping PR comment")

    reason = should_fail(delta, fail_on)
    if reason:
        print(f"\n  [FAIL] Condition met: {reason}")
        sys.exit(1)

    print("  [OK] Sentinel CI complete")
    print("")


if __name__ == "__main__":
    run_sentinel_ci()
