"""Evaluation harness.

Two things this measures, and one thing it refuses to pretend to measure.

1. Per-rule precision and recall against hand-labelled cases. This is an
   *intermediate* metric.
2. Invariance under meaning-preserving transforms. This is a correctness
   property, not a quality score.
3. NOT measured: whether flagging actually produces better diffs. That needs a
   human accept-rate study against `evals/task_metric.json`. The literature in
   this space is a warning here: a diagnostic that improved on every axis
   (held-out AUC 0.953) produced the worst real outcome, three times over.
   Precision is the proxy. Do not let it become the goal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import check_source
from .invariance import SENSITIVITY, STRICT
from .rules import RULES

EVALS = Path(__file__).resolve().parents[2] / "evals"


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    rule: str | None
    lang: str
    source: str
    expect: str  # "flag" | "clean"
    note: str = ""


@dataclass
class RuleScore:
    rule: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float | None:
        denom = self.tp + self.fp
        return round(self.tp / denom, 3) if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.tp + self.fn
        return round(self.tp / denom, 3) if denom else None

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "precision": self.precision,
            "recall": self.recall,
            "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
        }


def load_cases(paths: list[Path] | None = None) -> list[Case]:
    paths = paths or sorted(EVALS.glob("*/*.json"))
    cases: list[Case] = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data["cases"]:
            cases.append(
                Case(
                    id=entry["id"],
                    rule=entry.get("rule"),
                    lang=entry["lang"],
                    source=entry["source"],
                    expect=entry.get("expect", "flag"),
                    note=entry.get("note", ""),
                )
            )
        validate(cases[-len(data["cases"]) :] if data["cases"] else [])
    return cases


def validate(cases: list[Case]) -> None:
    """A case must be decidable: flagging a rule that does not apply is a bug."""
    for case in cases:
        if case.rule is None and case.expect != "clean":
            raise ValueError(f"{case.id}: wildcard cases must expect 'clean'")
        if case.lang not in ("python", "ts", "tsx"):
            raise ValueError(f"{case.id}: unknown lang {case.lang}")
        if not case.source.strip():
            raise ValueError(f"{case.id}: empty source")


def _fires(case: Case, rule: str | None) -> bool:
    rules = [rule] if rule else [r.id for r in RULES]
    findings = check_source(case.source, lang=case.lang, rules=rules, include_opt_in=True)
    return bool(findings)


def score(cases: list[Case]) -> dict[str, RuleScore]:
    scores = {r.id: RuleScore(r.id) for r in RULES}
    for case in cases:
        if case.rule is None:
            continue
        entry = scores[case.rule]
        fired = _fires(case, case.rule)
        if case.expect == "flag":
            if fired:
                entry.tp += 1
            else:
                entry.fn += 1
        else:
            if fired:
                entry.fp += 1
            else:
                entry.tn += 1
    return scores


def wildcard_failures(cases: list[Case]) -> list[Case]:
    """Cases asserting *nothing* should fire. Failures here are inversions."""
    return [c for c in cases if c.rule is None and _fires(c, None)]


def invariance(cases: list[Case], transforms=None) -> dict:
    strict = STRICT if transforms is None else transforms
    out = {"strict": {}, "sensitivity": {}}
    for name, fn in strict.items():
        failures = []
        errors = 0
        for case in cases:
            base = sorted(f.rule for f in check_source(
                case.source, lang=case.lang, include_opt_in=True))
            try:
                moved = fn(case.source, case.lang)
                after = sorted(f.rule for f in check_source(
                    moved, lang=case.lang, include_opt_in=True))
            except Exception as exc:  # a transform that breaks its own input
                errors += 1
                failures.append({"case": case.id, "error": str(exc)})
                continue
            if base != after:
                failures.append({"case": case.id, "before": base, "after": after})
        out["strict"][name] = {
            "passed": len(cases) - len(failures),
            "total": len(cases),
            "errors": errors,
            "failures": failures[:5],
        }
    for name, fn in (transforms is None and SENSITIVITY or {}).items():
        changed = 0
        moved_rules: dict[str, int] = {}
        for case in cases:
            base = sorted(f.rule for f in check_source(
                case.source, lang=case.lang, include_opt_in=True))
            try:
                moved = fn(case.source, case.lang)
                after = sorted(f.rule for f in check_source(
                    moved, lang=case.lang, include_opt_in=True))
            except Exception:
                continue
            if base != after:
                changed += 1
                for rule in set(base) ^ set(after):
                    moved_rules[rule] = moved_rules.get(rule, 0) + 1
        out["sensitivity"][name] = {
            "changed": changed, "total": len(cases),
            "rate": round(changed / max(len(cases), 1), 3),
            "rules_affected": dict(sorted(moved_rules.items(), key=lambda kv: -kv[1])),
        }
    return out


def task_metric() -> dict | None:
    path = EVALS / "task_metric.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if data.get("measured") else None


def write_calibration(scores: dict[str, RuleScore]) -> Path:
    """Rewrite `nits/calibration.py` with measured precision."""
    target = Path(__file__).with_name("calibration.py")
    lines = [
        '"""Per-rule precision, measured — not asserted.',
        "",
        'Generated by `nits eval --write` from `evals/labeled/cases.json`.',
        'Precision with no observed false positive is capped at 0.95: an unmeasured',
        'rule is not a perfect one.',
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "#: rule id -> (precision, n_positive, n_negative) from the labeled corpus",
        "MEASURED: dict[str, tuple[float, int, int]] = {",
    ]
    for rule, entry in scores.items():
        npos = entry.tp + entry.fn
        nneg = entry.fp + entry.tn
        if entry.precision is None:
            precision = 0.5
        elif entry.fp == 0:
            precision = 0.95
        else:
            precision = round(entry.tp / (entry.tp + entry.fp), 3)
        lines.append(f'    "{rule}": ({precision}, {npos}, {nneg}),')
    lines += [
        "}",
        "",
        "",
        "def precision_of(rule: str, default: float = 0.5) -> float:",
        '    """Lower of in-sample and held-out precision.',
        "",
        "    In-sample numbers come from the labeled corpus, which was written alongside",
        "    the rules. Held-out numbers come from hand-read samples on code that was",
        "    never used while writing a rule. The lower of the two is the honest one.",
        '    """',
        "    from .heldout import heldout_precision",
        "",
        "    values = [v for v in (",
        "        MEASURED.get(rule, (None,))[0],",
        "        heldout_precision(rule),",
        "    ) if v is not None]",
        "    return min(values) if values else default",
        "",
    ]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
