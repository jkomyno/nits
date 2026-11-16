"""Hand-adjudicated precision on held-out code.

The labeled corpus in `evals/` was written alongside the rules, so its precision
is in-sample and optimistic. These numbers come from sampling findings on
repositories that were never used while writing a rule, and reading each one
against the source with `nits fp --sample N --seed S`.

The same rule measures differently in different languages, and averaging that
away hides the finding. `narrator_comment` is 0.64 on TypeScript and 0.91 on
Rust: TypeScript type files attract label comments, Rust code comments its own
actions. A single global number would either over-trust the weak case or disable
a rule that is genuinely good in the strong one.

`precision_of` takes the lower of in-sample and held-out, preferring the
language-specific held-out sample when one exists.
"""

from __future__ import annotations

#: rule -> (true positives, findings read by hand), pooled across languages
HELDOUT: dict[str, tuple[int, int]] = {
    # 2 of the 5 TypeScript misses were comments labelling a type alias, which
    # the rule no longer reports; 0.76 is a floor, not an estimate.
    "narrator_comment": (19, 25),
    # `Err(_) => {}` and `Err(e) => Vec::new()`; the TypeScript/Python spellings
    # measured 2/2.
    "error_to_empty_success": (4, 4),
    # One dead block repeated across seven test cases.
    "commented_out_code": (7, 7),
    "junk_identifier": (1, 1),
}

#: "rule:lang" -> (true positives, findings read by hand)
HELDOUT_BY_LANG: dict[str, tuple[int, int]] = {
    # TypeScript: mock-setup narration is a true positive, fixture and schema
    # labels are not. Seed 5, ComposioHQ/composio ts/.
    "narrator_comment:ts": (9, 14),
    # Rust: comments restate the call below far more often. Seed 12, a public
    # Rust workspace.
    "narrator_comment:rust": (10, 11),
    # TypeScript best-effort `.catch(() => undefined)` both read as deliberate.
    "swallowed_error:ts": (2, 2),
    # Rust: `let _ = tracing_subscriber::try_init()` discards a Result by
    # design, which this spelling cannot distinguish from a dropped error.
    "swallowed_error:rust": (2, 4),
}


def _rate(entry: tuple[int, int] | None) -> float | None:
    if not entry or not entry[1]:
        return None
    return entry[0] / entry[1]


def heldout_precision(rule: str, lang: str | None = None) -> float | None:
    """Language-specific sample wins when it exists."""
    if lang is not None:
        specific = _rate(HELDOUT_BY_LANG.get(f"{rule}:{lang}"))
        if specific is not None:
            return specific
    return _rate(HELDOUT.get(rule))
