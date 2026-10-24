# Benchmark environment migration — 2026-10-24

Round 1 of the competitive benchmark installed its tools into whatever Python
was on `PATH`. Two environments were affected:

| Tool | Version used in round 1 | Lived in |
|---|---|---|
| ruff | 0.16.6 | hermes-agent venv (`%LOCALAPPDATA%\hermes\hermes-agent\venv`) |
| semgrep | 1.182.1 | hermes-agent venv |
| radon | 6.0.1 (ground truth) | hermes-agent venv |
| grimp | 3.17 (ground truth) | hermes-agent venv |
| bandit | 1.9.4 | global Python 3.10 (`%LOCALAPPDATA%\Programs\Python\Python310`) |

All five now live in a dedicated virtual environment:
`C:\Users\anton\Desktop\sentinel\bench-venv` (Python 3.11), with the exact
versions from `benchmarks/freeze.json`. `pip check` in that venv: clean.

## Recorded conflicts in the hermes-agent venv *before* touching it

`pip check` output (verbatim, pre-migration):

```text
hermes-agent 0.18.2 has requirement certifi==2026.5.20, but you have certifi 2026.10.23.
hermes-agent 0.18.2 has requirement PyJWT[crypto]==2.13.0, but you have pyjwt 2.15.1.
```

Root cause: semgrep 1.182.1 requires `certifi>=2026.10.19` and
`pyjwt[crypto]<3,>=2.15.0`. Installing it forced both packages past hermes'
pins. Neither conflict is hermes' own drift — the benchmark caused both.

Snapshot files in this directory:

* `hermes-pip-check-before.txt` — the two conflicts, verbatim.
* `hermes-freeze-before.txt` — full `pip freeze` of the hermes venv before migration.
* `python310-pip-check-before.txt` — pip check of global Python 3.10 before migration.

## Restoration procedure (per the round-1 review decision)

1. Record the conflicts (above) — done before any uninstall.
2. Uninstall the benchmark's packages from the hermes venv:
   `semgrep`, `ruff`, `radon`, `grimp`. Not touched: anything hermes declares
   itself. `bandit` was never in this venv.
3. Re-apply hermes' own pins for the packages semgrep forced up:
   `certifi==2026.5.20`, `PyJWT[crypto]==2.13.0` (from hermes'
   `pyproject.toml` `dependencies`).
4. Uninstall `bandit` from global Python 3.10 (it was the benchmark's, and it
   is a leaf application — no other package in that environment depends on it).
   The general-purpose `ruff 0.14.12` in Python 3.10 predates the benchmark
   (round 1 used 0.16.6 from the hermes venv) and was left alone.
5. Verify with `pip check` in the hermes venv — after-state in
   `hermes-pip-check-after.txt`; must report no broken requirements.

`ruff` for hermes' own development is declared in hermes' *dev* extra
(`ruff==0.15.10`); that extra was never installed in this venv (its `debugpy`
and `ty` pins are absent), so nothing hermes declared was removed by dropping
the benchmark's `ruff 0.16.6`. Re-install via `pip install -e .[dev]` from the
hermes checkout if hermes development happens in this venv.

## Raw snapshots

See `hermes-freeze-after.txt`, `hermes-pip-check-after.txt`,
`python310-bandit-uninstall.txt`, and `bench-venv-freeze.txt`.
