"""Quick smoke test for the CI script — standalone, no GitHub env needed."""
import os, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACTION_PATH = HERE.parent  # .github/actions/sentinel/
REPO_ROOT = HERE.parent.parent.parent.parent  # Sentinel/
SCAN_PATH = REPO_ROOT.parent / "test-repos" / "requests"

os.environ.setdefault("SENTINEL_ACTION_PATH", str(ACTION_PATH))
os.environ.setdefault("SENTINEL_SCAN_PATH", str(SCAN_PATH))
os.environ.setdefault("SENTINEL_BUDGET", "small")
os.environ.setdefault("SENTINEL_FAST_MODE", "true")
os.environ.setdefault("SENTINEL_FAIL_ON", "never")
os.environ.setdefault("SENTINEL_COMMENT_TITLE", "Test Comment")
os.environ.setdefault("SENTINEL_BASE_REF", "")
os.environ.setdefault("SENTINEL_EVENT_NAME", "push")
os.environ.setdefault("SENTINEL_GITHUB_TOKEN", "")
os.environ.setdefault("SENTINEL_CHECKPOINT_DIR", tempfile.mkdtemp())
os.environ.setdefault("GITHUB_REPOSITORY", "test-owner/test-repo")
os.environ.setdefault("GITHUB_EVENT_PATH", "")

sys.path.insert(0, str(HERE))
from ci import (
    _import_sentinel,
    scan_repo,
    _load_checkpoint,
    _save_checkpoint,
    compute_delta,
    format_comment,
    should_fail,
    run_sentinel_ci,
)

# 1. Test imports
SentinelAgent = _import_sentinel(ACTION_PATH)
print("  [PASS] Import SentinelAgent")

# 2. Test scan
result = scan_repo(SCAN_PATH, fast_mode=True, SentinelAgent=SentinelAgent, budget="small")
h = result["audit"]["health_score"]
f = result["files_scanned"]
changed = result["_pr_summary"]["changed_files"]
tests = result["_pr_summary"]["suggested_tests"]
print(f"  [PASS] Scan: health={h}, files={f}, changed={len(changed)}, tests={bool(tests)}")

# 3. Test checkpoint save/load
_save_checkpoint(Path(os.environ["SENTINEL_CHECKPOINT_DIR"]), result)
loaded = _load_checkpoint(Path(os.environ["SENTINEL_CHECKPOINT_DIR"]))
assert loaded["audit"]["health_score"] == h
print("  [PASS] Checkpoint save/load")

# 4. Test delta (against itself = zero delta)
delta = compute_delta(result, result)
assert delta["health_before"] == delta["health_after"]
assert delta["health_delta"] == 0
print(f"  [PASS] Delta (self): {delta['health_before']} -> {delta['health_after']} ({delta['health_delta']})")

# 5. Test delta with artificial shift
result2 = dict(result)
result2["audit"] = dict(result["audit"])
result2["audit"]["health_score"] = h - 10
result2["audit"]["risk_scores"] = result["audit"]["risk_scores"] + [
    {"file": "new_bad.py", "score": 90, "level": "high"}
]
delta2 = compute_delta(result, result2)
assert delta2["health_delta"] == -10
assert len(delta2["new_high_risk_files"]) == 1
print(f"  [PASS] Delta (shifted): {delta2['health_before']} -> {delta2['health_after']} ({delta2['health_delta']})")

# 6. Test comment formatting
comment = format_comment(delta2, result2, "Test Comment", "Sentinel Analysis")
assert "<!-- sentinel:analysis -->" in comment
assert "health" in comment.lower()
assert "new_bad.py" in comment
assert len(comment) > 100
print(f"  [PASS] Comment format: {len(comment)} chars, marker present")

# 7. Test fail conditions
assert should_fail(delta, "never") is None
assert should_fail(delta2, "drop-5") is not None
assert should_fail(delta2, "drop-10") is not None
assert should_fail(delta, "below-70") is None
print("  [PASS] Fail conditions")

# 8. Test full CI run (no PR, no token)
print("\n  --- Full CI run (no PR, no token) ---")
run_sentinel_ci()
print("  --- End ---")

print(f"\n  {'='*50}")
print(f"  ALL 8 TESTS PASSED")
print(f"  {'='*50}")
