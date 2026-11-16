"""Hand-adjudicated precision on held-out code.

The labeled corpus in `evals/` was written alongside the rules, so its precision
is in-sample and optimistic. These numbers come from sampling findings on code
that was never used while writing a rule: `ComposioHQ/composio`, `ts/` package,
400 files / 85,482 lines, sampled with `nits fp --sample N --seed S` and read by
hand against the source.

`precision_of` takes the minimum of in-sample and held-out, and the held-out
number is the one that gates the reward. Updating this file means re-running the
sample and reading the source, not adjusting a rule until the table looks good.
"""

from __future__ import annotations

#: rule -> (true positives, findings read by hand)
HELDOUT: dict[str, tuple[int, int]] = {
    # seed 777, n=22. Misses: fixture labels ("// CALCOM_AUTH"), why-comments
    # ("// Wrap the callback to handle errors"), type assertions.
    "narrator_comment": (15, 22),
    # seed 99 + 2024. All defensible: best-effort `.catch(() => undefined)`.
    "swallowed_error": (2, 2),
    # seed 99. `catch { return {} }` / `return null` where callers cannot tell
    # "empty" from "failed".
    "error_to_empty_success": (4, 4),
    # seed 99. Seven findings, one dead block repeated across seven test cases.
    "commented_out_code": (7, 7),
    "junk_identifier": (1, 1),
}


def heldout_precision(rule: str) -> float | None:
    entry = HELDOUT.get(rule)
    if not entry or not entry[1]:
        return None
    return entry[0] / entry[1]
