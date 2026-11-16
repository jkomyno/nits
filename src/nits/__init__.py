"""Public API.

    import nits
    nits.check_source(src, lang="ts")        # -> list[Finding]
    nits.check_path("src/x.py")              # -> Report
    nits.reward(src, lang="ts", tests_passed=1.0)
"""

from __future__ import annotations

from pathlib import Path

from .findings import Finding, Report
from .lang import lang_for_path, parse, walk  # noqa: F401  (re-exported for tools)
from .rules import RULE_BY_ID, RULES, make_ctx
from .calibration import MEASURED, precision_of

__all__ = [
    "Finding", "Report", "RULES", "RULE_BY_ID", "MEASURED",
    "check_source", "check_path", "check_paths", "reward", "precision_of",
]

_SUPPORTED = tuple(sorted({e for e in __import__("nits.lang", fromlist=["EXT_TO_LANG"]).EXT_TO_LANG}))


def _selected(rules: list[str] | None, include_opt_in: bool) -> list[str]:
    if rules:
        unknown = [r for r in rules if r not in RULE_BY_ID]
        if unknown:
            raise ValueError(f"unknown rule(s): {', '.join(unknown)}")
        return list(rules)
    return [r.id for r in RULES if r.default or include_opt_in]


def check_source(
    source: str | bytes,
    *,
    lang: str,
    rules: list[str] | None = None,
    include_opt_in: bool = False,
    min_precision: float | None = None,
) -> list[Finding]:
    """Run rules over `source`. Findings are deduplicated and line-sorted."""
    selected = _selected(rules, include_opt_in)
    ctx = make_ctx(source, lang)
    out: list[Finding] = []
    seen: set[tuple[str, int, int]] = set()
    for rule_id in selected:
        for finding in RULE_BY_ID[rule_id].fn(ctx):
            key = (finding.rule, finding.line, finding.col)
            if key in seen:
                continue
            seen.add(key)
            if min_precision is not None and precision_of(finding.rule) < min_precision:
                continue
            out.append(finding)
    out.sort(key=lambda f: (f.line, f.col, f.rule))
    return out


def check_path(
    path: str | Path,
    *,
    rules: list[str] | None = None,
    include_opt_in: bool = False,
    min_precision: float | None = None,
) -> Report | None:
    p = Path(path)
    lang = lang_for_path(p)
    if lang is None:
        return None
    source = p.read_text(encoding="utf-8", errors="replace")
    return Report(
        path=str(p),
        lang=lang,
        findings=check_source(
            source, lang=lang, rules=rules,
            include_opt_in=include_opt_in, min_precision=min_precision,
        ),
    )


def check_paths(
    paths,
    *,
    rules: list[str] | None = None,
    include_opt_in: bool = False,
    min_precision: float | None = None,
) -> list[Report]:
    reports = []
    for path in paths:
        report = check_path(
            path, rules=rules, include_opt_in=include_opt_in, min_precision=min_precision,
        )
        if report is not None:
            reports.append(report)
    return reports


def __getattr__(name: str):
    if name == "reward":
        from .reward import reward as _reward
        return _reward
    raise AttributeError(name)
