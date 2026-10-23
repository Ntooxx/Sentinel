# Contributing

## Setup

```bash
git clone https://github.com/Ntooxx/Sentinel.git
cd Sentinel
python -m pip install -e .
python -m pip install pytest ruff
```

## Running tests

```bash
python -m pytest -q -m "not perf"
python -m pytest -q -m perf
```

The active product is `src/sentinel2`. New tests belong in `tests/test_s2_*.py` and must exercise the real CLI/package, not the retired 1.x monolith.

## Code style

```bash
ruff check .
python -m compileall -q src tests tools sentinel.py
```

Sentinel has zero external runtime dependencies. Do not add new runtime packages without discussion.

## Quality rules

Thresholds belong in `src/sentinel2/config.py` and the rules JSON files, not scattered through modules. When changing analysis logic, bump `analysis_version` and regenerate both copies:

```bash
python - <<'PY'
from pathlib import Path
from sentinel2.config import write_default_rules
write_default_rules(Path("config/quality_rules.json"))
write_default_rules(Path("src/sentinel2/quality_rules.json"))
PY
```

## PR checklist

- [ ] Tests pass on Windows and Linux
- [ ] Ruff lint clean
- [ ] No new runtime dependencies
- [ ] No dashboard/MCP/chat/retrieval/autofix surface added
- [ ] CHANGELOG updated (if applicable)
- [ ] Report stays source-first and within the size budget

## Reporting issues

Include Sentinel version, Python version, OS, the command you ran, and minimal reproduction steps. For quality-report issues, attach the JSON report when possible.
