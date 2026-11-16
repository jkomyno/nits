"""Command line interface.

Advisory by default. `--gate` is opt-in and precision-filtered; that is the
whole difference from a mandatory linter.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import check_source, check_paths
from . import rules as rules_mod
from .calibration import precision_of
from .heldout import HELDOUT_BY_LANG
from .diff import git_diff, parse_diff
from .evaluate import (
    invariance, load_cases, score, task_metric, wildcard_failures, write_calibration,
)
from .findings import Finding
from .lang import lang_for_path
from .rules import RULES

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "out", "target", "vendor",
    ".venv", "venv", "env", ".nox", ".tox", "site-packages", "__pypackages__",
    ".eggs", ".next", ".turbo", "coverage", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".hypothesis", "fixtures", "testdata", "third_party", "3rdparty", "external",
    ".claude/worktrees", "worktrees",
}

C_DIM, C_RED, C_YEL, C_GRE, C_BOLD, C_OFF = (
    "\033[2m", "\033[31m", "\033[33m", "\033[32m", "\033[1m", "\033[0m",
)


def _color(enabled: bool, code: str, text: str) -> str:
    return f"{code}{text}{C_OFF}" if enabled else text


def render_findings(path: str, findings: list[Finding], color: bool, lang: str) -> str:
    if not findings:
        return ""
    out = [_color(color, C_BOLD, path)]
    for f in findings:
        prec = precision_of(f.rule, lang)
        tag = _color(color, C_DIM, f"p{prec:.2f}")
        loc = _color(color, C_DIM, f"{f.line:>5}:{f.col:<3}")
        out.append(f"  {loc} {tag}  {_color(color, C_YEL, f.rule):<28} {f.message}")
        if f.evidence:
            out.append(f"        {_color(color, C_DIM, '| ' + f.evidence)}")
    return "\n".join(out)


def _emit(reports, as_json: bool, color: bool) -> int:
    total = sum(len(r.findings) for r in reports)
    if as_json:
        print(json.dumps({
            "total": total,
            "reports": [r.to_dict() for r in reports if r.findings],
        }, indent=2))
        return total
    for report in reports:
        chunk = render_findings(report.path, report.findings, color, report.lang)
        if chunk:
            print(chunk)
    if total:
        print(f"\n{total} finding(s) across {sum(1 for r in reports if r.findings)} file(s). Advisory: nothing was blocked.")
    else:
        print("no findings")
    return total


def _apply_depth(args) -> None:
    if getattr(args, "max_depth", None):
        rules_mod.MAX_DEPTH = args.max_depth


def _skip_dir(name: str) -> bool:
    if name in SKIP_DIRS:
        return True
    # build output under non-standard names (dist-worker, dist_esm, ...)
    return name.startswith(("dist-", "dist_", "build-", "build_"))


_GENERATED = re.compile(r"\.min\.|-min\.|\.bundle\.|^worker-configuration\.d\.ts$")


def _iter_files(root: Path, max_lines: int = 20000):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
        for name in filenames:
            p = Path(dirpath) / name
            if not lang_for_path(p) or _GENERATED.search(name):
                continue
            try:
                if p.stat().st_size > max_lines * 40:
                    continue  # generated bundle, not reviewable code
            except OSError:
                continue
            yield p


def cmd_check(args) -> int:
    _apply_depth(args)
    paths: list[Path] = []
    for target in args.paths or ["."]:
        p = Path(target)
        if p.is_dir():
            paths.extend(_iter_files(p))
        elif p.exists():
            paths.append(p)
        else:
            print(f"not found: {target}", file=sys.stderr)
            return 2
    reports = check_paths(
        paths, rules=args.rules.split(",") if args.rules else None,
        include_opt_in=args.all, min_precision=args.min_precision,
    )
    _emit(reports, args.json, not args.no_color and sys.stdout.isatty())
    if args.gate is not None:
        gate_hits = [
            f for r in reports for f in r.findings
            if precision_of(f.rule, r.lang) >= args.gate
        ]
        if gate_hits:
            print(f"\ngate: {len(gate_hits)} finding(s) at precision >= {args.gate}", file=sys.stderr)
            return 1
    return 0


def cmd_diff(args) -> int:
    _apply_depth(args)
    diff_args = ["--staged"] if args.staged else [args.base]
    try:
        changed = parse_diff(git_diff(diff_args, cwd=args.cwd))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    reports = []
    for path, cf in changed.items():
        lang = lang_for_path(path)
        if lang is None:
            continue
        full = Path(args.cwd or ".") / path
        if not full.exists():
            continue
        source = full.read_text(encoding="utf-8", errors="replace")
        findings = check_source(
            source, lang=lang,
            rules=args.rules.split(",") if args.rules else None,
            include_opt_in=args.all,
            min_precision=args.min_precision,
        )
        scoped = [f for f in findings if cf.contains(f.line, f.end_line)]
        if scoped:
            from .findings import Report
            reports.append(Report(path=path, lang=lang, findings=scoped))
    _emit(reports, args.json, not args.no_color and sys.stdout.isatty())
    if args.gate is not None:
        gate_hits = [
            f for r in reports for f in r.findings
            if precision_of(f.rule, r.lang) >= args.gate
        ]
        if gate_hits:
            print(f"\ngate: {len(gate_hits)} finding(s) at precision >= {args.gate}", file=sys.stderr)
            return 1
    return 0


def cmd_eval(args) -> int:
    cases = load_cases()
    scores = score(cases)
    inversions = wildcard_failures(cases)
    if args.write:
        target = write_calibration(scores)
        print(f"wrote {target}", file=sys.stderr)

    payload = {
        "cases": len(cases),
        "per_rule": [s.to_dict() for s in scores.values()],
        "inversions": [c.id for c in inversions],
        "task_metric": task_metric(),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"{_color(True, C_BOLD, 'Per-rule accuracy')} — {len(cases)} labeled cases\n")
    print(f"  {'rule':<30} {'prec':>6} {'rec':>6}  {'tp':>3} {'fp':>3} {'fn':>3} {'tn':>3}")
    for s in scores.values():
        p = f"{s.precision:.2f}" if s.precision is not None else "   -"
        r = f"{s.recall:.2f}" if s.recall is not None else "   -"
        print(f"  {s.rule:<30} {p:>6} {r:>6}  {s.tp:>3} {s.fp:>3} {s.fn:>3} {s.tn:>3}")

    if inversions:
        print(f"\n{_color(True, C_RED, 'INVERSIONS')}: cases that should be silent but fired")
        for case in inversions:
            print(f"  - {case.id}")
    else:
        print(f"\n{_color(True, C_GRE, 'no inversions')} — every asserted-clean case stayed clean")

    task = payload["task_metric"]
    print()
    if task:
        print(f"{_color(True, C_BOLD, 'Task metric')} (human accept-rate):")
        print(f"  {json.dumps(task)}")
    else:
        print(_color(True, C_YEL, "Task metric: NOT MEASURED."))
        print("  Precision above is a proxy. Metrics that improved on every axis have")
        print("  produced the worst real outcomes in this literature; do not tune the")
        print("  gate against precision alone. Add evals/task_metric.json with")
        print('  {"measured": true, ...} once a human accept-rate study exists.')
    return 1 if inversions else 0


def cmd_invariance(args) -> int:
    cases = load_cases()
    report = invariance(cases)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{_color(True, C_BOLD, 'Strict invariance')} — findings must not move\n")
        ok = True
        for name, res in report["strict"].items():
            passed = res["passed"] == res["total"] and not res["errors"]
            ok = ok and passed
            mark = _color(True, C_GRE, "PASS") if passed else _color(True, C_RED, "FAIL")
            print(f"  {mark} {name:<24} {res['passed']}/{res['total']}"
                  + (f"  ({res['errors']} transform error(s))" if res["errors"] else ""))
            for failure in res["failures"][:3]:
                print(f"         {failure}")
        print(f"\n{_color(True, C_BOLD, 'Sensitivity')} — known coupling, reported not hidden\n")
        for name, res in report["sensitivity"].items():
            print(f"  {name:<24} {res['changed']}/{res['total']} cases changed "
                  f"({res['rate']:.0%}) — text-based rules are name/word coupled")
        return 0 if ok else 1
    return 0


def cmd_fp(args) -> int:
    """Density and sampling, for adjudicating false positives by hand."""
    _apply_depth(args)
    root = Path(args.path)
    files = list(_iter_files(root))[: args.limit]
    import random
    rng = random.Random(args.seed)
    total_lines = 0
    per_rule: dict[str, int] = {}
    pool: list[tuple[str, Finding, str]] = []
    for path in files:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lang = lang_for_path(path)
        lines = source.splitlines()
        total_lines += source.count("\n") + 1
        for f in check_source(
            source, lang=lang, rules=args.rules.split(",") if args.rules else None,
            include_opt_in=args.all,
        ):
            per_rule[f.rule] = per_rule.get(f.rule, 0) + 1
            pool.append((str(path), f, lines))
    density = 100 * len(pool) / max(total_lines, 1)
    print(f"files={len(files)} lines={total_lines} findings={len(pool)} density={density:.2f}/100 lines")
    for rule, count in sorted(per_rule.items(), key=lambda kv: -kv[1]):
        print(f"  {rule:<30} {count:>5}  ({100 * count / max(total_lines, 1):.2f}/100)")
    if args.sample:
        print(f"\n{_color(True, C_BOLD, 'sample for adjudication')} (seed {args.seed})")
        for path, f, lines in rng.sample(pool, min(args.sample, len(pool))):
            src = lines[f.line - 1].strip()[:110] if 0 < f.line <= len(lines) else "?"
            print(f"  {path}:{f.line}  {f.rule}  p={precision_of(f.rule, lang):.2f}")
            print(f"      {src}")
    return 0


def cmd_rules(args) -> int:
    langs = ("ts", "python", "rust")
    header = "  " + f"{'rule':<30} {'default':>8} {'prec':>6} " + " ".join(f"{l:>6}" for l in langs)
    print(header)
    print("  " + f"{'':<30} {'':>8} {'pooled':>6} " + " ".join(f"{'':>6}" for _ in langs))
    for rule in RULES:
        # `~` marks a column with no language-specific sample of its own: the
        # number shown is the pooled one, which is not the same claim.
        per = " ".join(
            f"{precision_of(rule.id, l):>6.2f}" if f"{rule.id}:{l}" in HELDOUT_BY_LANG
            else f"{precision_of(rule.id, l):>5.2f}~"
            for l in langs
        )
        print(f"  {rule.id:<30} {str(rule.default):>8} {precision_of(rule.id):>6.2f} {per}  {rule.summary}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nits", description="Diff-scoped slop checks with published precision.")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--rules", help="comma-separated rule ids")
        sp.add_argument("--all", action="store_true", help="include opt-in rules")
        sp.add_argument("--min-precision", type=float, default=None)
        sp.add_argument("--max-depth", type=int, default=None, help="nesting threshold (default 5)")
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--no-color", action="store_true")

    pc = sub.add_parser("check", help="scan paths, advisory")
    pc.add_argument("paths", nargs="*")
    pc.add_argument("--gate", type=float, default=None, help="exit 1 above this precision")
    common(pc)
    pc.set_defaults(func=cmd_check)

    pd = sub.add_parser("diff", help="only lines added by a change")
    pd.add_argument("--base", default="HEAD")
    pd.add_argument("--staged", action="store_true")
    pd.add_argument("--cwd", default=None)
    pd.add_argument("--gate", type=float, default=None)
    common(pd)
    pd.set_defaults(func=cmd_diff)

    pe = sub.add_parser("eval", help="per-rule precision/recall + inversions")
    pe.add_argument("--write", action="store_true", help="update calibration.py")
    pe.add_argument("--json", action="store_true")
    pe.set_defaults(func=cmd_eval)

    pi = sub.add_parser("invariance", help="meaning-preserving transforms")
    pi.add_argument("--json", action="store_true")
    pi.set_defaults(func=cmd_invariance)

    pf = sub.add_parser("fp", help="density + sample for false-positive adjudication")
    pf.add_argument("path")
    pf.add_argument("--limit", type=int, default=400)
    pf.add_argument("--sample", type=int, default=0)
    pf.add_argument("--seed", type=int, default=7)
    common(pf)
    pf.set_defaults(func=cmd_fp)

    pr = sub.add_parser("rules", help="list rules and measured precision")
    pr.set_defaults(func=cmd_rules)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
