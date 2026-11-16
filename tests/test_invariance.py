"""Invariance and reward.

The invariance tests are the ones that would fail on a byte-grid or indentation
based implementation, which is why they are strict tests rather than reported
metrics.
"""

from __future__ import annotations

import pytest

import nits
from nits.evaluate import load_cases
from nits.invariance import STRICT, reindent, rename_catch_binding, use_tabs
from nits.reward import reward

CASES = load_cases()


def findings(source: str, lang: str) -> list[str]:
    return sorted(f.rule for f in nits.check_source(source, lang=lang, include_opt_in=True))


@pytest.mark.parametrize("name", list(STRICT))
def test_strict_transforms_preserve_findings(name: str):
    transform = STRICT[name]
    failures = []
    for case in CASES:
        before = findings(case.source, case.lang)
        after = findings(transform(case.source, case.lang), case.lang)
        if before != after:
            failures.append((case.id, before, after))
    assert not failures, f"{name} moved findings: {failures[:3]}"


def test_reindent_infers_indent_unit():
    """A 1-space file must not be collapsed to column zero."""
    src = "def f(x):\n if x:\n  return 1\n return 2\n"
    out = reindent(src, "python", 2)
    assert out.splitlines()[1] == "  if x:"
    assert findings(out, "python") == findings(src, "python")


def test_tabs_are_equivalent():
    src = "def f(x):\n    if x:\n        return 1\n"
    assert findings(use_tabs(src, "python"), "python") == findings(src, "python")


def test_catch_rename_cannot_hide_a_swallow():
    src = "def f():\n    try:\n        return parse()\n    except E as err:\n        _ = err\n"
    assert "swallowed_error" in findings(src, "python")
    assert "swallowed_error" in findings(rename_catch_binding(src, "python"), "python")


# --------------------------------------------------------------------------- #
# Reward
# --------------------------------------------------------------------------- #

CLEAN = "def add(a, b):\n    return a + b\n"
MESSY = (
    "def f():\n"
    "    try:\n"
    "        return parse()\n"
    "    except Exception:\n"
    "        pass\n"
)


def test_failing_tests_zero_the_reward():
    assert reward(MESSY, lang="python", tests_passed=0) == 0.0
    assert reward(CLEAN, lang="python", tests_passed=0.0) == 0.0


def test_edited_tests_zero_the_reward():
    assert reward(CLEAN, lang="python", tests_passed=1.0, tests_unchanged=False) == 0.0


def test_clean_code_outscores_messy_code():
    assert reward(CLEAN, lang="python", tests_passed=1.0) > reward(
        MESSY, lang="python", tests_passed=1.0
    )


def test_cap_is_one_and_a_quarter():
    assert reward(CLEAN, lang="python", tests_passed=1.0) <= 1.25


def test_growth_is_penalised():
    """Padding cannot buy score: the comparator's Rust target grew 56% and scored higher."""
    padded = CLEAN + "\n\n\n\n\n\n\n\n"
    flat = reward(CLEAN, lang="python", tests_passed=1.0, baseline_source=CLEAN)
    grown = reward(padded, lang="python", tests_passed=1.0, baseline_source=CLEAN)
    assert grown < flat


def test_partial_test_pass_zeroes_the_reward():
    """A clean, half-broken solution must not outscore a correct, slightly dirty one."""
    assert reward(CLEAN, lang="python", tests_passed=0.99) == 0.0
    assert reward(MESSY, lang="python", tests_passed=1.0) > 0.0


@pytest.mark.parametrize("pad", [1, 2, 4, 8, 20])
def test_padding_dirty_code_cannot_buy_score(pad: int):
    """At 4-5 findings per 100 lines, a few blank lines used to outweigh the penalty."""
    base = MESSY + "".join(f"x{i} = {i}\n" for i in range(15))
    flat = reward(base, lang="python", tests_passed=1.0, baseline_source=base)
    padded = reward(base + "\n" * pad, lang="python", tests_passed=1.0, baseline_source=base)
    assert padded < flat


def test_reward_is_invariant_under_reindentation():
    assert reward(CLEAN, lang="python", tests_passed=1.0) == reward(
        reindent(CLEAN, "python", 2), lang="python", tests_passed=1.0
    )


def test_reward_is_invariant_under_catch_renaming():
    src = "def f():\n    try:\n        return parse()\n    except E as err:\n        _ = err\n"
    assert reward(src, lang="python", tests_passed=1.0) == reward(
        rename_catch_binding(src, "python"), lang="python", tests_passed=1.0
    )
