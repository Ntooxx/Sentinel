# Sentinel competitive benchmark — round 1

**Date:** 2026-10-24 · **State:** frozen (`benchmarks/freeze.json`, package `0a6b142390db`,
`analysis_version` 9, commit `939bee9`) · **Results:** `benchmarks/results/round1/`
· **Raw findings:** 5.7 MB of JSON, including every finding from all four tools.

**Machine:** Windows 11 (10.0.26200), AMD64 24 cores, local SSD, Python 3.11.14.

| Tool | Version | Invocation |
|---|---|---|
| Sentinel | 2.0.0 | `python -m sentinel2 scan <repo> --format json` |
| ruff | 0.16.6 | `ruff check --isolated --no-cache --output-format json <repo>` |
| bandit | 1.9.4 | `bandit -r <repo> -f json -q` |
| semgrep | 1.182.1 | `semgrep scan <repo> --config p/default --json --quiet --metrics=off` |

---

## Headline results

| Question | Result | Reading |
|---|---|---|
| **Precision@5** — are the top 5 real? | **0.80** (20 genuine, 1 debatable, 4 not genuine, n=25) | Three repositories perfect, two at 0.60 |
| **Actionability** — would you act on it? | **3.8 of 5** per repository (19/25) | Single reviewer; see limitations |
| **Recall on seeded defects** (inside each tool's own scope) | Sentinel **19/19**, bandit 10/10, ruff 4/4, semgrep 4/4 | The fixture discriminates on **scope**, not on recall |
| **Breadth** — defects each tool is even in scope for | Sentinel **19/25**, bandit 10, ruff 4, semgrep 4 | Sentinel's real advantage |
| **Confirmed bugs it missed** | **2** — both `NameError` at import, found by ruff | Sentinel has no name resolution |
| **Total wall time** across the corpus | Sentinel **10.9 s**, ruff 0.6 s, bandit 20.3 s, semgrep 92.7 s | ruff is faster; Sentinel beats bandit 2× and semgrep 8× |
| **Peak memory** | Sentinel **35–47 MB**, ruff 12–84, bandit 46–93, semgrep **496–627** | 12–18× less than semgrep |
| **Its own perf gate** (`tools/benchmark.py --check` on fastapi) | **Failed once, passed once** across two runs | Cold straddles the 2.0 s budget |

---

## 1. Method

**Freeze.** `tools/competitive_bench.py freeze` hashed the whole `src/sentinel2` package before
anything ran. Every stage refuses to run while the hash differs, and
`tests/test_s2_benchmark.py::test_freeze_manifest_matches_the_analysis_package` fails CI the same
way. **No rule, threshold or scoring value changed during this round** — the corrections listed in
[`PROBE_NOTES.md`](PROBE_NOTES.md) are all fixture-and-expectation corrections, and the package hash
is identical before and after them.

**Corpus.** Five real repositories that were *not* part of rubric calibration
([`corpus.json`](corpus.json)): requests (124 files), flask (193), fastapi (1,147), pytest (684),
thealgorithms-python (1,729). Three were cloned fresh for this round.

**Expectations were pre-registered.** The semgrep `p/default` inventory (1,073 rules, 2.5 MB) was
downloaded *before* the run; bandit's plugins were read from its installed source; ruff's effective
default set was established with a probe in an empty directory. Where a probe showed a catalogue
rule does not actually fire in the shipped configuration, the defect was recorded as **OUT** for
that tool rather than counted as a miss or a hit. See [`PROBE_NOTES.md`](PROBE_NOTES.md).

**Ground truth for the labels** came from the repositories, not from Sentinel
([`labels.json`](labels.json)):

* `radon` 6.0.1 for an independent cyclomatic-complexity count;
* `grimp` 3.17 (the engine behind *import-linter*) plus [`tools/verify_cycle_claims.py`](../tools/verify_cycle_claims.py)
  for import graphs;
* direct line counts for every size rule;
* Python 3.14.2 for every parse-error finding;
* actually importing two suspect modules to confirm a `NameError`.

**Reproduce:**

```bash
python tools/competitive_bench.py freeze
python tools/competitive_bench.py defects --run benchmarks/results/round1
python tools/competitive_bench.py corpus  --run benchmarks/results/round1
python tools/competitive_bench.py evaluate --run benchmarks/results/round1
python tools/sync_defect_lines.py --check     # manifest line numbers still match the fixture
```

---

## 2. Precision@5

| Repository | Band | Genuine | Debatable | Not genuine | P@5 | Actionable |
|---|---|---:|---:|---:|---:|---:|
| requests | maintained 95 | 4 | 0 | 1 | **0.80** | 3 |
| flask | maintained 94 | 3 | 1 | 1 | **0.60** | 3 |
| fastapi | maintained 95 | 5 | 0 | 0 | **1.00** | 5 |
| pytest | maintained 92 | 5 | 0 | 0 | **1.00** | 5 |
| thealgorithms-python | adequate 72 (low confidence) | 3 | 0 | 2 | **0.60** | 3 |
| **Mean** | | **20** | **1** | **4** | **0.80** | **3.8 / 5** |

Every size and complexity claim was independently confirmed. `radon` agreed with Sentinel exactly on
six of the ten complexity findings (`19/19`, `19/19`, `16/16`, `26/26`, `31/31`, `34 vs 35`) and
within a few branches on the rest (`39 vs 45`, `41 vs 44`, `27 vs 29`, `37 vs 40`) — all ten sit
above the threshold under either counter. Every line count (`697`, `561`, `1,625`, `1,126`, `4,691`,
`2,460`, `2,501`, `2,613`) matched the file exactly.

### The four that did not survive review

1. **requests #1 — `ARCH002` import cycle: false positive.**
   Sentinel reports a 7-module cycle closing with `requests.utils -> requests`. grimp builds the same
   graph (18 modules, 55 edges — identical to the verifier's 55) and finds **zero cycles**; no module
   imports the package root. The edge only exists because `_resolve_imports`
   (`analyze.py:162-163`) emits an edge to the *enclosing package* for every `from . import X`.
   Under that reading almost any package that imports its own submodules looks cyclic — and indeed
   the same reading inflates flask's cycle from 14 modules (grimp) to the 20 Sentinel reports.

2. **flask #4 — `MNT005` "Long function: Blueprint, 111 lines": mislabelled class.**
   The symbol at line 18 is `class Blueprint(SansioBlueprint)`. Its longest function is `__init__` at
   **36 lines**; the class body is 111. `findings.py:482-490` selects symbols by length/complexity
   and never filters `symbols.kind`, so a class body is reported as a long function with a
   recommendation to "extract one named step".

3. **thealgorithms #1 — `MNT009` "f-string expression part cannot include a backslash":
   interpreter artefact.** The file parses cleanly on Python 3.14.2 and the repository declares
   `requires-python = ">=3.15"`, where PEP 701 makes it legal. Sentinel parses with its own
   interpreter and **never states which one**. Scaling up: of the **90** parse errors Sentinel
   reported in this repository, **84 parse fine on 3.14** and only **6 are syntax errors in any
   version**.

4. **thealgorithms #4 — `MNT009` on `class SegmentTree[T]`: same artefact.**
   PEP 695 generic classes, legal since 3.12; parses on 3.14.

flask #1 was graded **debatable** rather than genuine: the cycle is real (grimp confirms a strongly
connected component of 14 modules), but the evidence overstates it by ~40%.

---

## 3. Recall on seeded defects — and what it really measures

The fixture holds 26 entries (25 seeded defects plus one deliberate decoy). Each defect carries the
rule every tool is *documented* to have for it; anything else is OUT and excluded from that tool's
denominator.

| Tool | In scope | Hits | Misses | Recall | OUT |
|---|---:|---:|---:|---:|---:|
| **Sentinel** | **19** | 19 | 0 | **100%** | 7 |
| bandit | 10 | 10 | 0 | 100% | 16 |
| ruff | 4 | 4 | 0 | 100% | 22 |
| semgrep | 4 | 4 | 0 | 100% | 22 |

100% across the board is not a triumph — it says the fixture is a *capability* test, and that the
useful signal is **breadth**: Sentinel covers architecture, tests, documentation, dependency and
hygiene defects the other three do not even claim, while ruff and semgrep cover correctness and
security patterns Sentinel does not. The decoy (an `md5` call with `usedforsecurity=False`) was
correctly ignored by all four.

What would make round 2 discriminating on recall rather than breadth: harder in-scope cases where a
tool plausibly fails its own rule (see §6).

---

## 4. Missed problems

Every high-severity competitor finding that landed on a **non-test, non-documentation source file**
was checked by hand ([`labels.json`](labels.json) → `competitor_high_severity_review`):

| Repo | Tool | Finding | Verdict | In Sentinel's top 5? |
|---|---|---|---|---|
| thealgorithms | ruff `F821` | `disjoint_set.py:13` undefined `Node` | **genuine — importing the module raises `NameError`** | file never mentioned |
| thealgorithms | ruff `F821` | `merge_sort_linked_list.py:16` undefined `Node` | **genuine — same, confirmed by import** | file never mentioned |
| thealgorithms | ruff `F821` | `double_linear_search_recursion.py:7` | genuine (Sentinel does flag this file, as `MNT009`) | yes |
| thealgorithms | bandit `B324` | `hashes/sha1.py:135` SHA-1 | not genuine — the file *implements* SHA-1, already `# noqa: S324` | — |
| flask | ruff `F821` | `helpers.py:679` `BaseExceptionGroup` | not genuine — artefact of `--isolated`; ruff run inside the repo honours `requires-python >= 3.11` and reports nothing | — |
| flask | bandit `B324` | `sessions.py:281` SHA-1 digest method | debatable — defensible security note, no Sentinel rule for it | — |
| pytest | semgrep ×2 | `importlib.resources` "only on 3.7+" | not genuine — pytest requires ≥3.9 | — |
| fastapi | semgrep ×16, ruff ×10 | docs and `docs_src` examples | not genuine as *source* findings — all 16 semgrep "high" hits are translated markdown | excluded by design |

**Overlap between Sentinel's top-5 files and the competitors' high-severity files: 0 of 5
repositories.** The tools are not measuring the same thing. The one place Sentinel is unambiguously
behind is **name resolution**: two confirmed `NameError`-at-import bugs in real source files that
Sentinel never mentions, because its catalogue has no equivalent rule and its limitations do not say
so.

---

## 5. Performance and resources

Wall time, median of 3 for Sentinel cold/warm, single run for competitors (ms):

| Repository | files | Sentinel cold | warm | report-only | ruff | bandit | semgrep |
|---|---:|---:|---:|---:|---:|---:|---:|
| requests | 124 | 568 | 368 | 120 | **69** | 926 | 6,552 |
| flask | 193 | 670 | 370 | 125 | **70** | 1,294 | 9,223 |
| fastapi | 1,147 | 2,057 | 469 | 120 | **274** | 5,380 | 27,622 |
| pytest | 684 | 2,309 | 422 | 122 | **119** | 6,596 | 25,689 |
| thealgorithms-python | 1,729 | 2,563 | 518 | 127 | **118** | 6,082 | 23,583 |
| **Total** | | | | | **0.6 s** | 20.3 s | **92.7 s** (Sentinel 10.9 s) |

Peak process-tree RSS, sampled at 50 ms (MB):

| Repository | Sentinel | ruff | bandit | semgrep |
|---|---:|---:|---:|---:|
| requests | 36 | 12 | 46 | **500** |
| flask | 35 | 12 | 54 | **496** |
| fastapi | 43 | 48 | 82 | **627** |
| pytest | 47 | 84 | 93 | **551** |
| thealgorithms-python | 43 | 42 | 55 | **554** |

Index size 0.7–7.7 MB. Bands: four `maintained`, thealgorithms `adequate` with `confidence: low`
(reason recorded: *"90 source parse error(s)"*).

**Honest reading:**

* **ruff is faster than Sentinel at everything** — 0.6 s total versus 10.9 s. It is a single-pass
  linter with no index; Sentinel pays for AST analysis, SQLite writes and report construction.
* Sentinel beats bandit ~2× and semgrep ~8× on wall time, and uses **12–18× less memory than
  semgrep** (35–47 MB versus 496–627 MB).
* The warm/cold ratio is 4–5×, which is what the incremental index is supposed to buy.

**Its own budgets are not reliably met.** `sentinel_2.md` §2.6 sets fastapi cold ≤ 2.0 s, warm
≤ 0.4 s, report ≤ 0.1 s. Measured on this machine: cold **1.99 s / 2.06 s / 2.65 s** across three
runs, warm **449–485 ms**, report **98–121 ms**. The project's own gate
(`tools/benchmark.py --check`, +20% tolerance) **failed on one run and passed on the next** — cold
variance alone crosses it. On a 1,147-file checkout of fastapi this is a near-miss, not a pass.

---

## 6. Where Sentinel is worse — backlog (deliberately not fixed during this round)

Each item was found by this benchmark. Fixing any of them changes `src/sentinel2` and therefore
breaks the freeze, so they are recorded here rather than applied:

| # | Defect | Evidence | Suggested fix |
|---|---|---|---|
| **A** | Parse errors are reported as high-severity repository defects with no mention of *which interpreter parsed the file*. On a repo requiring ≥3.15, **84 of 90** reported errors are artefacts of scanning with 3.11. | §2 items 3–4; `parse_errors_thealgorithms.txt` | Record `sys.version` in `scan`/`limitations`; compare against `requires-python`; downgrade or rephrase when the repository targets a newer interpreter than the scanner |
| **B** | `ARCH002` counts an edge to the *enclosing package* for every `from . import X`, producing a **false cycle** (requests: grimp finds none) and **inflating counts** (flask 14 → 20) | `analyze.py:162-163`, `tools/verify_cycle_claims.py` | Emit only `resolved.alias` when `node.module` is empty; keep the parent edge out of cycle detection |
| **C** | `MNT005` "Long function" fires on **class bodies** (`symbols.kind` never filtered) | flask `blueprints.py`: class 111 lines, longest function 36 | Filter to `kind in ('function', 'async_function')`, or retitle when the symbol is a class |
| **D** | The spec's top-5 noise cap is **computed and then discarded**: `report.py:85` builds `top`, uses it only for `actions`, and every renderer displays `findings[:5]` of the uncapped list. **5 of 5 repositories show a different top 5 than the cap produces**, and three violate §4.5's "at most 2 per category" (thealgorithms shows 4× `MNT009`) | §4.5 vs `renderers/text.py:112`, `markdown.py:49`, `html.py:525` | Publish `top` in the JSON and render it; `--all-findings` keeps the full list |
| **E** | No name resolution → **missed two confirmed `NameError`-at-import bugs** ruff found; the limitation list does not say name resolution is absent | §4 | Either add a conservative undefined-name rule, or state plainly in `limitations` that names are not resolved |
| **F** | fastapi performance budgets are **not reliably met** on this machine | §5 | Profile the cold path; the gate currently flaps |
| **G** | The report never discloses the parsing interpreter, and the JSON has no field for it | grep for `interpreter`/`sys.version` in `sentinel2`: no matches | Add `scan.python_version` to the schema |

Two further observations that are not defects but matter for positioning:

* **Competitors are far noisier.** bandit emitted 4,751 findings on fastapi and 5,395 on pytest
  (mostly import-blacklist and test-code hits); semgrep put 16 "high" findings on translated
  documentation. Sentinel produced 41 and 115. A ranking claim has to be made against tools that
  bury the signal — this is the strongest evidence for Sentinel's design.
* **The cap fix (D) changes what users see.** The capped list swaps repeated size/complexity entries
  for test-mapping findings (`TST006`, `TST003`) that were *not* reviewed here. Whether that is an
  improvement is an open question for round 2, not an assumption.

---

## 7. What this benchmark does not establish

* **One reviewer, not blinded.** Verdicts and especially `actionable` are a single reviewer's
  judgment. Nothing here should be quoted externally without a second reviewer who has not seen
  Sentinel's output.
* **Five repositories**, not twenty; three of them mature open source clustered in the
  `maintained` band.
* **No SonarQube, CodeScene, Qlty or DeepSource.** None install and run natively on this machine
  (SonarQube needs a JVM server, Qlty/CodeScene are not available as local Windows CLIs). Semgrep
  was used in its OSS `p/default` configuration; its secrets rules stay silent there, which is
  recorded as OUT rather than scored.
* **ruff ran `--isolated`** for comparability, which cost it one false positive (flask) that it would
  not produce inside the repository. Standardising the configuration trades realism for
  comparability; both readings are reported.
* **Performance is one machine**, 24 cores, Windows Defender active. medians of three for Sentinel,
  single runs for competitors.
* **The fixture is synthetic.** 100% recall on it does not predict recall on real defects — the two
  missed `NameError`s in §4 are the counter-evidence.

---

## 8. Round 2 protocol

1. **Unfreeze deliberately**: implement A–F, bump `analysis_version`, regenerate the rules JSON, and
   write a *new* `benchmarks/freeze.json` recording which defects were fixed and why.
2. **Add in-scope defects that should be hard**: cases where Sentinel plausibly fails its own rules,
   so recall discriminates instead of every tool scoring 100%.
3. **Second reviewer, blinded**: hand over `labels.json` with the rule IDs and tool stripped,
   keeping only file+line+evidence, and compare verdicts before reporting agreement.
4. **Grow the corpus to 15–20** and include at least two repositories that land below `maintained`,
   matching the gap already documented in `docs/CALIBRATION.md`.
5. **Re-run the same harness unchanged** (`corpus`, `defects`, `evaluate`) so round 1 and round 2
   are comparable, and publish both — including any regression.
