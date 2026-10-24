# Benchmark probe notes

Evidence recorded **before** the scored run, so that every expectation in
[`fixtures/defect_corpus/defects.json`](fixtures/defect_corpus/defects.json) can be traced to a
tool's real behaviour instead of to its marketing page. Nothing here was collected after seeing a
score.

Date: 2026-10-24. Machine: Windows 11, Python 3.11.14.

## Tools under test

| Tool | Version | How it was run |
|---|---|---|
| Sentinel | 2.0.0 (analysis_version 9, package `0a6b142390db`) | `python -m sentinel2 scan <repo> --format json` |
| ruff | 0.16.6 | `ruff check --isolated --no-cache --output-format json <repo>` |
| bandit | installed under Python 3.10 | `bandit -r <repo> -f json -q` |
| semgrep | 1.182.1 | `semgrep scan <repo> --config p/default --json --quiet --metrics=off` |

## 1. Semgrep rule inventory (p/default)

`https://semgrep.dev/c/p/default` was downloaded before the run: **2,520,189 bytes, 1,073 rule ids**.
Rules relevant to this fixture, as they appear in that file:

- `python.lang.security.audit.eval-detected.eval-detected`
- `python.lang.security.audit.subprocess-shell-true.subprocess-shell-true`
- `python.lang.security.deserialization.pickle.avoid-pickle`
- `python.lang.security.audit.formatted-sql-query.formatted-sql-query`
- `generic.secrets.security.detected-github-token.detected-github-token`
- `python.flask.security.audit.hardcoded-config.avoid_hardcoded_config_DEBUG`

## 2. Capability probes (separate probe files, not the scored fixture)

Probe A - `eval`, `shell=True`, `pickle.loads`, formatted SQL, `os.system`, hardcoded password,
GitHub PAT - run with `--config p/default`:

| Construct | Fired | Rule |
|---|---|---|
| `eval(raw)` | yes | `eval-detected` |
| `subprocess.run(..., shell=True)` | yes | `subprocess-shell-true` |
| `pickle.loads(blob)` | yes | `avoid-pickle` |
| `query = "SELECT ..." % x` then `db.execute(query)` | yes | `formatted-sql-query` |
| `os.system("cat " + name)` | **no** | no p/default rule targets `os.system` |
| `PASSWORD = "Sup3rSecret!Passw0rd"` | **no** | no generic hardcoded-password rule in p/default |
| `API_TOKEN = "ghp_<38 chars>"` | **no** | `detected-github-token` present but silent in OSS scan mode; `--config p/secrets` also silent. Secrets detection is gated, so the entry is recorded as OUT, not as a miss. |
| `DEBUG = True` (module level) | **no** | the only DEBUG rule matches `$M.config['DEBUG'] = True` |

Consequence: semgrep is in scope for D01, D02, D04 and D11 only, and each of those is marked in
`defects.json` with the rule id it must emit.

Probe B - `ruff check --isolated` on a single file containing only `"%s" % (x,)`, in a directory
with no configuration anywhere:

```
codes: ['UP031']
codes with -s E,F: []
```

So ruff 0.16.6's effective default set under `--isolated` is wider than the documented
`E4/E7/E9/F`: it also fired `PLW1510` and `RUF022` on the fixture. The scope note in
`defects.json` records what was actually run rather than what the documentation claims.

Probe C - bandit was read from its installed source
(`...\site-packages\bandit`) to confirm `B105` matches variable names containing `token`, that
`B324` exempts `usedforsecurity=False`, and that `B608` fires on string-built SQL. All three
behaviours were then observed on the fixture during the harness dry run.

## 3. Corrections made before the scored run

These are fixture and expectation corrections, all made because a seeded defect did not contain
the defect it claimed to contain. **No Sentinel rule, threshold or scoring value was touched** - the
analysis package hash in `freeze.json` is unchanged across all of them.

| Was | Problem | Now |
|---|---|---|
| D10 seeded a branch-conditionally assigned name | pyflakes correctly does not report that as F821; ruff was scored a "miss" for staying silent | the fixture uses a genuinely undefined name |
| D16 expected ruff `E999` | ruff 0.16.6 reports parse failures as `invalid-syntax` | expectation uses the code the tool emits |
| D14 expected a per-file TODO finding | `MNT008` is a project-level density rule and the 4,700-line generated file diluted the density below the threshold | marker count raised to 24 and the generated file cut to ~2,100 lines, which puts density at ~10 per 1,000 lines |
| D19 generated a long function full of ternaries | Sentinel reports a symbol through one rule chain (`MNT007 > MNT006 > MNT005`), so it was recorded as a complexity defect and `MNT005` never fired | the generated function is straight-line, so the only defect it has is its length |
| D03/D05/D06 expected semgrep rules | probes in section 2 show no p/default rule fires for those constructs | recorded as OUT with the probe result as the rationale |
| D24 did not exist | bandit's `B324` finding had no home in the manifest | added as its own defect with `usedforsecurity=False` kept alongside as a decoy |