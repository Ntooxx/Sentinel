"""Show the full Sentinel CI feature: scan, delta, and the PR comment."""
import os, sys, tempfile, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACTION_PATH = HERE.parent
REPO_ROOT = HERE.parent.parent.parent.parent
SCAN_PATH = REPO_ROOT.parent / "test-repos" / "requests"

os.environ["SENTINEL_ACTION_PATH"] = str(ACTION_PATH)
os.environ["SENTINEL_SCAN_PATH"] = str(SCAN_PATH)
os.environ["SENTINEL_BUDGET"] = "small"
os.environ["SENTINEL_FAST_MODE"] = "true"
os.environ["SENTINEL_FAIL_ON"] = "drop-5"
os.environ["SENTINEL_BASE_REF"] = "main"
os.environ["SENTINEL_EVENT_NAME"] = "pull_request"
os.environ["SENTINEL_GITHUB_TOKEN"] = ""
os.environ["SENTINEL_CHECKPOINT_DIR"] = tempfile.mkdtemp()
os.environ["GITHUB_REPOSITORY"] = "psf/requests"
os.environ["GITHUB_EVENT_PATH"] = ""

sys.path.insert(0, str(HERE))
from ci import _import_sentinel, scan_repo, compute_delta, format_comment, should_fail

SentinelAgent = _import_sentinel(ACTION_PATH)

print("=" * 70)
print("  STEP 1: Scan the repository (simulating PR head)")
print("=" * 70)
head = scan_repo(SCAN_PATH, fast_mode=True, SentinelAgent=SentinelAgent, budget="small")
h = head["audit"]["health_score"]
metrics = head["audit"]["metrics"]
risks = head["audit"]["risk_scores"]
suggestions = head.get("suggestions", [])
change = head["_pr_summary"]["changed_files"]
tests = head["_pr_summary"]["suggested_tests"]
print(f"  Health: {h}/100")
print(f"  Files: {metrics.get('total_files')}  Lines: {metrics.get('total_lines'):,}  TODOs: {metrics.get('total_todos')}")
print(f"  High-risk files: {sum(1 for r in risks if r.get('level') == 'high')}")
print(f"  Suggestions: {len(suggestions)}")
print(f"  Changed files (from git): {change}")
print(f"  Suggested test commands: {tests}")

print()
print("=" * 70)
print("  STEP 2: Simulate a 'before' scan (health slightly different)")
print("=" * 70)
base = dict(head)
base["audit"] = dict(head["audit"])
base["audit"]["health_score"] = h + 8  # pretend base was healthier
base["audit"]["risk_scores"] = head["audit"]["risk_scores"][:-1]  # one less risk
base["audit"]["metrics"] = dict(head["audit"]["metrics"])
base["audit"]["metrics"]["total_todos"] = metrics.get("total_todos", 0) - 3
base["audit"]["health_score_data"] = dict(head["audit"].get("health_score_data", {}))
base_cats = dict(base["audit"]["health_score_data"].get("categories", {}))
head_cats = dict(head["audit"].get("health_score_data", {}).get("categories", {}))
# shift a category to show delta
for k in base_cats:
    base_cats[k] = dict(base_cats[k])
    base_cats[k]["score"] = base_cats[k].get("score", 100) + 5

print(f"  Base health: {base['audit']['health_score']}/100")
print(f"  Head health: {head['audit']['health_score']}/100")

print()
print("=" * 70)
print("  STEP 3: Compute the health delta")
print("=" * 70)
delta = compute_delta(base, head)
print(f"  Health: {delta['health_before']:.0f} -> {delta['health_after']:.0f} ({delta['health_delta']:+.1f})")
print(f"  New high-risk files: {len(delta['new_high_risk_files'])}")
print(f"  Worsened risk files: {len(delta['worsened_risk_files'])}")
print(f"  TODOs: {delta['todos_before']} -> {delta['todos_after']}")

print()
print("=" * 70)
print("  STEP 4: Format the PR comment (this is what gets posted)")
print("=" * 70)
comment = format_comment(delta, head, "Sentinel Analysis", "Sentinel Analysis")
out = Path(tempfile.gettempdir()) / "sentinel_demo_comment.md"
out.write_text(comment, encoding="utf-8")
print(f"  Comment written to: {out}")
print(f"  Length: {len(comment)} chars")

print()
print("=" * 70)
print("  STEP 5: Check fail condition")
print("=" * 70)
fail_reason = should_fail(delta, "drop-5")
print(f"  fail-on: drop-5")
print(f"  Health delta: {delta['health_delta']:+.1f}")
result = 'fail_reason' if fail_reason else 'No fail - CI passes'
print(f"  {result}")
print()

print("=" * 70)
print("  FEATURE COMPLETE")
print("=" * 70)
