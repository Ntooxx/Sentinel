"""Complexity and maintainability defects."""


def classify(value: int, mode: str, flag_a: bool, flag_b: bool) -> str:
    # D12 - cyclomatic complexity far above the MNT007 threshold of 25.
    score = 0
    if value < 0:
        score += 1
    elif value == 0:
        score += 2
    elif value < 10:
        score += 3
    elif value < 100:
        score += 4
    elif value < 1000:
        score += 5
    else:
        score += 6
    if mode == "alpha":
        score += 1
    elif mode == "beta":
        score += 2
    elif mode == "gamma":
        score += 3
    elif mode == "delta":
        score += 4
    elif mode == "epsilon":
        score += 5
    elif mode == "zeta":
        score += 6
    else:
        score -= 10
    if flag_a and flag_b:
        score *= 2
    elif flag_a or flag_b:
        score *= 3
    elif not flag_a and not flag_b:
        score = 0
    if value % 2 == 0:
        score += 1
    if value % 3 == 0:
        score += 2
    if value % 5 == 0:
        score += 4
    if value % 7 == 0:
        score += 8
    if score > 100:
        score = 100
    elif score > 50:
        score = 50
    elif score > 25:
        score = 25
    if "retry" in mode:
        score += 1
    if "cache" in mode:
        score += 2
    if "async" in mode:
        score += 3
    if "sync" in mode:
        score += 4
    return f"{score}:{value}:{flag_a}:{flag_b}"


# D14 - TODO/FIXME density across the project, all of it concentrated here.
# TODO: revisit the classify() thresholds once metrics land.
# FIXME: classify() ignores mode strings longer than 32 characters.
# TODO: the scoring below is a placeholder for the real weighting.
# FIXME: negative inputs take the same path as zero.
# TODO: drop the modulo chain once the profiler data exists.
# FIXME: flag_a and flag_b should be a single enum argument.
# TODO: cache the string formatting, it runs per request.
# HACK: score is clamped by hand instead of a saturating helper.
# TODO: add a unit test for every branch before touching this again.
# FIXME: "epsilon" and "zeta" are dead modes in production.
# TODO: document what a negative final score means.
# XXX: this whole function is a candidate for a lookup table.
# TODO: extract the modulo chain into a small helper.
# FIXME: the retry/cache/async/sync checks overlap with mode checks.
# TODO: split classify() into mode parsing and scoring.
# FIXME: the early clamp at 100 hides real overflow in the modulo chain.
# TODO: replace the magic strings with a module-level enum.
# FIXME: score is threaded through five branches with no single owner.
# TODO: benchmark this before the refactor, it runs on every request.
# FIXME: mode strings are compared case-sensitively with no normalisation.
# TODO: write the property test for the clamp first.
# XXX: two of these branches are unreachable in production traffic.
# TODO: decide whether negative scores are meaningful at all.
# FIXME: the returned tuple format is parsed by two callers.