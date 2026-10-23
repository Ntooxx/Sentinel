# Design decisions

Sentinel is small on purpose. This document records the decisions that shaped it and, where
possible, the measurement that justified each one. It is written for a reader who wants to know
*why* the tool behaves the way it does, including the decisions that removed features.

The through-line: **Sentinel would rather make a smaller claim it can defend than a larger one it
cannot.** Most of what follows is an instance of that.

---

## 1. The overall result is a coarse band, not a letter grade

**Decision.** Publish `maintained` / `adequate` / `weak` / `unmaintained`. Keep letter grades for
individual dimensions only. (`summary.overall_grade` was removed from the JSON contract in
schema 2 and replaced by `summary.band`.)

**Why.** Five of the six dimensions saturate near A+ on any mature, well-tested codebase:
architecture only fires on import cycles and a missing entry point; docs, dependencies and security
only on a small number of findings. Only maintainability discriminates, and it carries 0.25 weight
against roughly 0.75 weight of near-constant A+. The fine overall grade was therefore reading as
six-dimensional evidence while being almost entirely a function of test ratio, size and the grade
ceiling.

The clearest symptom: Django scored 75 on maintainability and click scored 78, despite Django
carrying roughly 35× the real debt in high-complexity functions and oversized files. That is not a
tuning failure. The inputs do not contain the difference, so no weight or threshold can produce it.

**Consequence.** The tool no longer claims to distinguish a B+ from an A−, because it cannot. Bands
allow ties, which is honest: two repositories can both be `maintained` without being ranked.

---

## 2. The findings are the headline; the band is secondary

**Decision.** Every renderer leads with **what to fix first** — five specific findings with evidence,
a reason, and a next step — and treats the band as context below it. The previous order (grade,
then dimensions, then findings) is reversed.

**Why.** Of the two artifacts, only one is independently useful. "198 web assets were classified as
source", "`build.py` is 1,041 lines at complexity 60 with 82 tests mapped to it", "84 oversized test
files" are specific, true and actionable today. A letter grade generated every calibration
argument. The findings earn their keep; the grade was the liability.

---

## 3. A scale penalty was built, measured, and deleted

**Decision.** Remove the scale factor and its ceiling. Raw size is not charged against the overall
score.

**Why.** Large clean codebases saturate the dimensions, so size was the only remaining signal
separating a mid-sized library from a very large framework. A saturating penalty on analysed lines
was added, then swept:

| Penalty | Mean band distance | Max distance | Verdict |
|---:|---:|---:|---|
| 0.08 (current) | 1.07 | 3 | baseline |
| 0.20 | 1.79 | 4 | worse |
| 0.30 | 2.64 | 6 | much worse |
| 0.20 at 250k reference | 1.21 | 3 | worse |

Every configuration was worse than doing nothing. Raising it moved `black` from A to C+ and
`pydantic` from B+ to C−, while never moving the small project it was introduced to reach. Scale is
a real cost, but it is already charged to maintainability through complexity and file size, and a
blunt scalar charged large clean codebases for the crime of being large.

**Consequence.** Simpler scoring, and `analysis_version` bumped so no stale cached score survives.
The reasoning is recorded here because "we tried this and removed it" is worth more than the knob.

---

## 4. A finding is judged on the population it actually covers

**Decision.** Penalties are proportional to the share of code affected, with an absolute offset so
a single problem in a small repository is not catastrophic — but families at or above
`penalty_population_floor` (100 files) drop the offset entirely.

**Why.** The offset assumes a small denominator. Applied to a large one it inverts into forgiveness.
Django has 2,341 test files and 84 very large ones; with the offset the share was
`84 / (2341 + 150)` ≈ 3.4%, which a half-share curve flattened to a rounding error and the tests
dimension read A+.

**Consequence.** This was a bug fix, not a calibration. It moved `pydantic` from B+ to B on its own
merits.

---

## 5. Templates and stylesheets are not source

**Decision.** Remove `html` and `css` from `SOURCE_LANGUAGES`.

**Why.** They were classified as source, but templates and static assets are exempt from the
generated/vendor/example/test paths, so they could never map to a test. They manufactured
"unmapped hotspot" findings. Django had 198 web assets classified as source; `awesome-python` had 9
of its 16, including `style.css`, `main.js` and a Jinja template, while the real hotspot —
`build.py` at 1,041 lines and complexity 60 — was mapped and covered by 82 tests.

---

## 6. A calibration harness with expectations written before the output

**Decision.** `tools/calibrate.py` scans a corpus of real repositories and checks observed results
against expected bands. Expectations live in `config/calibration_corpus.json`, each with a
rationale and a quality tier.

**Why.** "Authoritative" should be a claim with evidence behind it. The corpus makes calibration
reproducible instead of asserted, and `--check` turns acceptance criteria into a gate rather than a
paragraph.

**Consequence, stated plainly in the corpus itself:** the expectations were refined while looking at
results, so they are not fully independent. That caveat is written into the file rather than left
for a reviewer to discover.

---

## 7. `calibrated` is still `false`

**Decision.** Do not flip the flag, even though the band gate now passes.

**Why.** The gate passes because 13 of 14 scorable repositories land in `maintained`. The corpus has
exactly one negative example below it (`thealgorithms-python`). A gate with one negative example does
not demonstrate that `adequate`, `weak` and `unmaintained` are assigned correctly — it demonstrates
that the corpus is too easy. Flipping the flag on top of that would be the hollow claim the flag
exists to prevent.

**Consequence.** Reports still say `(provisional)`. The path to flipping it is written down in
[`CALIBRATION.md`](CALIBRATION.md): add corpus entries that actually land in the lower bands, get a
second reviewer, re-run, then flip.

---

## 8. The statistics were also demoted, not just reported

**Decision.** Replace the tier-mean separation check with a band-monotonicity check that permits
ties. Keep rank correlation as informational only, with the reason stated.

**Why.** The old check was degenerate: tier 3 and tier 4 each held a single repository, so "tier 3
mean ≥ tier 2 mean" was really "`awesome-python` outscores the tier-2 group." A gate with n=1 groups
is not measuring separation. Separately, at n=14 the minimum detectable Spearman correlation at
α = 0.05 is about 0.53, so the corpus cannot validate a rank claim in either direction.

---

## 9. Zero runtime dependencies, standard library only

**Decision.** No third-party runtime packages. Python 3.10+.

**Why.** The tool must run in environments where installing anything is a negotiation — CI images,
air-gapped machines, locked-down corporate laptops. A quality tool that cannot run is not a quality
tool. It also removes an entire class of supply-chain and version-drift problems.

**Consequence.** Some things are hand-rolled that a library would do faster. That trade was made
deliberately, and the cost is visible in the code rather than hidden in a dependency tree.

---

## 10. Incremental SQLite index, not a stateless scan

**Decision.** Keep a local SQLite index at `.sentinel/quality.db` (WAL, foreign keys, 20-scan
retention) and re-read only changed files.

**Why.** Re-scanning everything on each run makes `watch` unusable and makes `report` slow. The index
stores per-file facts keyed by size, mtime and analysis version, so a warm scan only reads what
changed. `report` reads the stored report JSON and never rescans or rebuilds ASTs.

**Consequence.** A cache invalidation problem that must be handled explicitly: `analysis_version`
is bumped whenever analysis logic changes, so stale scores can never survive a rule change. The
version was bumped twice during the work recorded here.

---

## 11. Documentation is metadata by default

**Decision.** Do not read full documentation content unless `--include-docs` is passed.

**Why.** Doc trees are large, low-signal for the six dimensions, and dominated by translation
directories. Reading them by default would slow every scan to produce findings that mostly describe
other projects' localisation choices.

---

## 12. Three commands, and everything else was deleted

**Decision.** The public surface is `scan`, `report`, `watch`. The 1.x feature set — dashboard, HTTP
server, MCP bridge, retrieval/chat, PR summaries, coverage ingestion, autofix, memory, timeline,
alerts — was removed from the active product.

**Why.** Every one of those features was a separate claim to maintain, document and defend. The
product is a local triage instrument; each addition diluted that. The 2.0 rewrite is a deliberate
contraction.

**Consequence.** The retired modules moved to [`legacy/`](../legacy) and are excluded from linting and from the scan's default directories. The active package does not import them. The 1.x code and its 197 tests are preserved and still runnable with `python -m pytest legacy/tests`.

---

## 13. Determinism and a versioned contract

**Decision.** Sort every list in the report. Treat the JSON schema as a contract and version it
(schema 2 changed `summary.overall_grade` → `summary.band`).

**Why.** A report that reorders between runs cannot be diffed, and the JSON is meant to be consumed
by automation. Breaking the contract was necessary when the band replaced the grade, so it was
versioned and documented rather than changed silently — and a stored report from an older schema is
rejected with a rescan instruction instead of being rendered with a missing field.
