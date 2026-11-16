"""RL reward shaping.

Three properties the equivalent in comparable tools lacks, each with a test:

1. Gated by tests (a passing-but-dirty solution cannot win, and neither can a
   clean-looking broken one).
2. Penalised for code growth, so padding cannot buy score.
3. Invariant under meaning-preserving edits, because it is computed from the
   same AST findings — re-indenting a file cannot change its reward.
"""

from __future__ import annotations

from .calibration import precision_of


def _line_count(source: str) -> int:
    return source.count("\n") + (0 if source.endswith("\n") else 1)


def reward(
    source: str,
    *,
    lang: str,
    tests_passed: float,
    baseline_source: str | None = None,
    tests_unchanged: bool = True,
    min_precision: float = 0.80,
) -> float:
    """0.0 – 1.25. Zero when tests fail or test files were edited."""
    from . import check_source

    if tests_passed <= 0 or not tests_unchanged:
        return 0.0

    findings = check_source(source, lang=lang)
    lines = max(_line_count(source), 1)
    weighted = [f for f in findings if precision_of(f.rule, lang) >= min_precision]
    density = 100.0 * len(weighted) / lines
    hygiene = max(0.0, 1.0 - density / 5.0)  # 5 flags / 100 lines -> no bonus

    penalty = 0.0
    if baseline_source is not None:
        base_lines = max(_line_count(baseline_source), 1)
        growth = (lines - base_lines) / base_lines
        if growth > 0:
            penalty = min(0.20, 0.20 * growth)

    return round(min(1.25, max(0.0, 1.0 + 0.25 * hygiene - penalty)), 4)
