# Calibration

Sentinel's overall quality claim is marked **provisional** until the rubric has been calibrated
against real repositories. This document records what was calibrated, how, and how well the
rubric currently agrees with manual review. It exists so that "authoritative" is a claim with
evidence behind it rather than an adjective.

## The contract

From [`sentinel_2.md`](../sentinel_2.md) section 4.2:

> `provisional` is true until the rubric has been calibrated on at least 10 repositories and the
> `rules.calibrated` flag is true.

So two things must hold before the `(provisional)` marker disappears from a report:

1. the rubric has been tuned against a corpus of real repositories;
2. `calibrated` in [`config/quality_rules.json`](../config/quality_rules.json) is `true`.

`quality.compute_quality` then only marks a report provisional when the scan's own confidence is
low. `--fail-under` is safe to use as a gate either way.

## Why the claim is a band, not a letter

The overall result is one of four coarse bands:

| Band | Score floor | Meaning |
|---|---:|---|
| `maintained` | 80 | Tests, docs and dependency hygiene are in place. |
| `adequate` | 70 | Usable, but carries real debt worth fixing. |
| `weak` | 60 | Significant structural problems; risky to depend on. |
| `unmaintained` | 0 | Little or no evidence of maintenance practice. |

The labels describe maintenance **evidence**, not commit activity. A dormant but well-tested
library can still be `maintained`; a busy repository with no tests and no docs can still be
`unmaintained`.

Letter grades still exist internally for dimension evidence and for the `--fail-under` flag, but
the overall result no longer publishes one. The reason is measured, not aesthetic: five of the six
dimensions (`architecture`, `docs`, `dependencies`, `security`, and usually `tests`) saturate near
A+ for mature code. Only maintainability moves, and it carries 0.25 weight against roughly 0.75
weight of near-constant A+. A fine overall grade therefore reads as six-dimensional evidence while
being almost entirely a function of test ratio, size and the grade ceiling. Bands deliberately
allow ties: two repositories can both be `maintained` without the tool pretending to rank them.

## How to run it

```bash
git clone --depth 1 <each repo in config/calibration_corpus.json> /tmp/corpus
python tools/calibrate.py --corpus /tmp/corpus --check
```

`tools/calibrate.py` scans each repository with the same CLI users run, maps the observed result
to a band, and reports it against the band implied by the expected grade. `--check` exits non-zero
unless the acceptance criteria in [`config/calibration_corpus.json`](../config/calibration_corpus.json)
hold.

`python tools/calibrate.py --clone` clones anything missing first. `--json` emits the full result
for tracking over time.

Expected grades are unchanged by the band change. The expected band is a pure function of the
grade that was written during manual review, so the expectations were not edited to fit the new
output.

## The corpus

Fifteen repositories chosen to span quality levels, size, archetype and language mix: mature
frameworks (`django`, `fastapi`, `pydantic`, `pytest`), clean libraries (`click`, `flask`,
`jinja`, `httpx`, `black`, `requests`, `rich`), a small utility project (`awesome-python`), a tiny
dormant library (`records`), a weak collection (`thealgorithms-python`) and a docs-only control
(`github/gitignore`) that must not receive a source grade.

Each entry carries an expected grade **written from codebase review**, plus a `rationale` stating
the evidence and a `tier` (1 excellent … 4 weak) used for the separation check.

## Acceptance criteria

| Criterion | Bar | Why |
|---|---:|---|
| Scorable repositories | ≥ 12 | The blueprint's minimum is 10. |
| Maximum band distance | ≤ 1 | A repository may be one band from review, never two. |
| Mean band distance | ≤ 0.5 | The average error must be under half a band. |
| Tier separation | required | Clearly weaker code must not land in a higher band. |
| Low-confidence scans | ≤ 2 | Most of the corpus must be analysable with high confidence. |
| Docs-only control | no source grade | A link list must not get architecture/maintainability grades. |

Tier separation is a band-monotonicity check, not a comparison of tier means. For every pair of
tiers, every repository in the better tier must be in an equal-or-better band than every
repository in the worse tier. Ties are allowed; only strict inversions fail. The previous
tier-mean check was degenerate because tier 3 and tier 4 each held a single repository, so
"tier 3 mean ≥ tier 2 mean" was really "awesome-python outscores the tier-2 group."

Rank correlation is computed and reported but is **not** an acceptance gate. Thirteen of the
fifteen repositories are mature open source clustered in the same band, where the expected
ordering is a matter of taste, so correlation over the whole set measures noise. With fourteen
scorable points the minimum detectable correlation at α = 0.05 is roughly 0.53, so the corpus is
also too small to validate a rank claim either way.

## Current status

The corpus was last run at fine-grade resolution. The table below is **projected** from those
recorded observed grades through the band mapping, not a fresh run; re-run the harness to confirm.

| Repository | Expected band | Projected band | Distance |
|---|---|---|---:|
| black | maintained | maintained | 0 |
| click | maintained | maintained | 0 |
| thealgorithms-python | adequate | adequate | 0 |
| records | maintained | maintained | 0 |
| flask | maintained | maintained | 0 |
| jinja | maintained | maintained | 0 |
| httpx | maintained | maintained | 0 |
| pydantic | maintained | maintained | 0 |
| requests | maintained | maintained | 0 |
| rich | maintained | maintained | 0 |
| fastapi | maintained | maintained | 0 |
| pytest | maintained | maintained | 0 |
| django | maintained | maintained | 0 |
| awesome-python | maintained | maintained | 0 |

At band resolution every repository agrees with review, and the monotonicity check finds no
inversion. The gate therefore passes at the resolution the product now claims — but that pass is
weak evidence, because thirteen of fourteen scorable repositories land in one band. The corpus has
exactly one negative example below `maintained` (`thealgorithms-python`). It does not yet test
whether `adequate`, `weak` or `unmaintained` are assigned correctly in general.

**`calibrated` is therefore still `false`, and reports still say `(provisional)`.** A band gate
that passes on one negative example is not enough to call the band authoritative.

## What the calibration changed

Each of these was a defect the corpus exposed, not a knob turned to match output:

* **Fixture secrets no longer fail a repository.** Secret-like matches in test, fixture,
  certificate and sample paths are still reported in full, but weighted at 10% of a live match.
  Django's `tests/certs/*.key` and Sentinel's own redaction tests had been driving Security to B/F.
* **Provider-shaped tokens are weighted above name-based matches.** `AKIA…`, `ghp_…`, `sk-…` carry
  high confidence and a large penalty; the `assigned credential` heuristic carries medium
  confidence. A single real credential in live source drops Security to a C.
* **Secret detection precision.** `token = '…'` inside a docstring example matched the
  name-based rule. Values must now look like an opaque credential token.
* **Dynamic execution and debug checks are advisory.** `pickle.loads` in a cache backend, `exec`
  in a shell command and `DEBUG` in a debug view are legitimate framework design. These rules now
  carry a small penalty and a capped family, and are labelled as review notes.
* **Test-coverage shortfall is proportional.** A 0.02 test ratio over 1571 modules used to cost the
  same 8 points as a mild shortfall. It now scales with the gap, in its own capped family.
* **A failing dimension pulls the overall down.** A weighted average let healthy dimensions rescue
  `thealgorithms-python` to B- despite D grades in tests and maintainability. It is now C-.
* **Penalties are proportional to the share of code affected**, with an absolute offset so a single
  problem in a twenty-file repository is not catastrophic and a large repository is not forgiven
  its hundreds. This is what `sentinel_2.md` section 4.2 asks for.
* **Large populations are judged on their real population.** The offset above made 84 very large
  test files in a 2341-file suite indistinguishable from three, because the share became 3%.
  Families at or above `penalty_population_floor` no longer take the offset.
* **Generated data blobs are not maintainability findings.** A 41k-line Python file holding one
  big literal is data. It was producing "very large source file" findings and forcing sampling,
  which dragged confidence down for `black`.
* **Templates and stylesheets are not source.** HTML and CSS were classified as source, so web
  assets could never map to a test and manufactured unmapped-hotspot findings. They are not
  production code for the six dimensions and are now classified as assets.
* **A scale penalty was tried and removed.** Charging the overall for raw size was measured across
  four configurations. Every one was worse than the existing rubric: it moved `black` from A to C+
  and `pydantic` from B+ to C− while never moving the small project it was meant to reach. Scale
  is a real cost, but it is already charged to maintainability through complexity and file size,
  and a blunt scalar charged large clean codebases for being large.
* **Deleted-but-tracked files are not "unreadable".** `git ls-files` lists them; they were inflating
  the skipped-file count and the coverage denominator.

## Why it is not yet authoritative

At fine-grade resolution the rubric systematically rated large mature projects (`django`,
`fastapi`, `rich`, `pytest`) an A where review said B+. Both readings were defensible — those
repositories have exceptional tests and documentation — and that disagreement is exactly what
motivated the coarse band. The tool can defend "this is maintained code"; it could not defend
"this is a B+ and that is an A−."

Two caveats remain, stated plainly:

* **The expectation set is not independent.** Expected grades were refined while looking at
  results. Some revisions were genuine factual corrections (each is recorded in
  `calibration_corpus.json` with its evidence), but the set as a whole needs review by someone who
  has not seen the tool's output before the bands are called authoritative outside this project.
* **The lower bands are barely tested.** Only `thealgorithms-python` lands below `maintained`. Until
  the corpus contains genuinely mediocre, abandoned-but-real and empty repositories, the tool's
  ability to distinguish `adequate` from `weak` from `unmaintained` is unmeasured.

## Before flipping `calibrated`

1. Add corpus entries that actually land in `adequate`, `weak` and `unmaintained`, so the band
   classifier is tested on more than one negative example.
2. Get the expectation set reviewed independently; commit any corrections with evidence.
3. Re-run `python tools/calibrate.py --corpus <path> --check` and commit the output alongside the
   corpus revision that produced it.
4. Only then set `calibrated: true` in `src/sentinel2/config.py`, regenerate
   `config/quality_rules.json` and `src/sentinel2/quality_rules.json`, and bump `analysis_version`.

Until then, treat the band as a well-evidenced estimate and read the findings, not just the band.
