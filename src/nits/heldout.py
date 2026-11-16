"""Hand-adjudicated precision on held-out code.

The labeled corpus in `evals/` was written alongside the rules, so its precision
is in-sample and optimistic. These numbers come from sampling findings on
repositories that were never used while writing a rule, and reading each one
against the source with `nits fp <path> --rules R --sample N --seed S`.

A finding is a true positive when the code matches what the rule says it
reports, as a reviewer reading the source would judge it. Whether acting on a
true positive makes the code better is the task metric, which is not measured
here.

The same rule measures differently in different languages, and averaging that
away hides the finding. `precision_of` takes the lower of in-sample and held-out,
preferring the language-specific sample when one exists and falling back to the
pooled sample otherwise.

A sample read before a rule was fixed stays in the count after the fix, and
later samples add to it. The number is then a floor: it still includes misses
the rule no longer makes.
"""

from __future__ import annotations

#: rule -> (true positives, findings read by hand) for samples recorded without
#: a language split. They count toward the pooled number only.
HELDOUT: dict[str, tuple[int, int]] = {
    # `Err(_) => {}` and `Err(e) => Vec::new()`; the TypeScript/Python spellings
    # measured 2/2.
    "error_to_empty_success": (4, 4),
    # One dead block repeated across seven test cases.
    "commented_out_code": (7, 7),
    "junk_identifier": (1, 1),
}

# Samples marked "2026-11-16" were drawn with seed 7 from these trees, using the
# rules as of nits 530ce3c:
#   durable-streams/durable-streams@82f9963ae0b489566352393be9b4796c788c99c2
#   OrchestratorInc/agent-orchestrator@450507193ee3988ded574547132c749256ab8454
#   jkomyno/uniku@66dfbdeff0d8a7483beaa98b83c106f6092efb29
#   jkomyno/esmwell@0fe5d07af718736e5a3cd59b7fd839c268789015
#   djc/instant-distance@dc0bc5a2191c3b5c76db4645f25db4d3bf753b96
#
# Samples marked "fresh" were drawn with seed 7 after the fixes those samples
# prompted, from trees never read while writing or fixing a rule, using the
# rules as of nits 97825ad (swallowed_error on TypeScript: 43ef647):
#   mastra-ai/mastra@ff4106b87bef3535162f2d812b6f42a8a9fd1924
#   an external TypeScript library
#   the "Rust workspace (external)" of the README's density table
#   mvanhorn/last30days-skill@ae8c32327f86f516894a2d94edc6127314465708

#: "rule:lang" -> (true positives, findings read by hand)
HELDOUT_BY_LANG: dict[str, tuple[int, int]] = {
    # TypeScript: 9/14 at seed 5 on ComposioHQ/composio ts/, where mock-setup
    # narration was a true positive and fixture and schema labels were not (2 of
    # those 5 misses were type-alias labels the rule no longer reports).
    # 2026-11-16: 18/35. Misses: 7 byte-layout labels, 3 section headers drawn
    # with box characters, 6 comments that add a reason, unit or date, 1 test
    # label. Fresh: 9/10. Miss: `// biSizeImage`, a field label.
    "narrator_comment:ts": (36, 59),
    # Rust: 10/11 at seed 12 on a public Rust workspace. 2026-11-16: 1/1.
    "narrator_comment:rust": (11, 12),
    # 2026-11-16: 6/6.
    "narrator_comment:python": (6, 6),
    # TypeScript: best-effort `.catch(() => undefined)`, 2/2. 2026-11-16: 8/14.
    # Misses: 3 discards explained by a comment above the statement, 2 queue
    # tails whose source promise is returned to the caller, 1 null result that
    # the caller checks. Fresh: 10/10, mostly test teardown.
    "swallowed_error:ts": (20, 26),
    # Rust: `let _ = tracing_subscriber::try_init()` discards a Result by design,
    # which this spelling cannot distinguish from a dropped error, 2/4.
    # 2026-11-16: 3/5. Misses: 2 `let _ = x?.search(..)` where `?` already
    # propagated the error and the discarded value is not a Result. Fresh: 5/10.
    # Misses: 2 `let _ = x.unwrap()` (since fixed), `try_init()` and
    # `install_default()` discarded by design, a `let _: T = ...` type check.
    "swallowed_error:rust": (10, 19),
    # 2026-11-16: 4/7. Misses: 3 handlers explained by a comment inside the
    # `except` block, which the rule did not see. Fresh: 8/10. Misses: 2
    # fallback assignments read as aliases (since fixed).
    "swallowed_error:python": (12, 17),
    # 2026-11-16: 19/20. Miss: `return null // Invalid URL encoding`.
    "error_to_empty_success:ts": (19, 20),
    # Fresh: 8/8.
    "error_to_empty_success:python": (8, 8),
    # Fresh: 0/1. Miss: `Err(TryRecvError::Empty) => None`, where "empty" is
    # not a failure. One finding is all the held-out Rust code produced.
    "error_to_empty_success:rust": (0, 1),
    # Fresh: 10/10, all `process.env.X || 'password'`-style defaults in tests.
    "config_fallback:ts": (10, 10),
    # 2026-11-16: 3/3, all `TODO: Implement ...` above a stub.
    "elision_comment:ts": (3, 3),
    # 2026-11-16: 4/9. Misses: 5 functions whose depth counted `catch` as a level
    # on top of its `try`. Fresh: 10/10, each with five or more real levels.
    "deep_nesting:ts": (14, 19),
    # 2026-11-16: 4/4.
    "deep_nesting:python": (4, 4),
    # 2026-11-16: 1/1.
    "deep_nesting:rust": (1, 1),
    # 2026-11-16: 8/8, numbered `res1`/`res2`/`data1` in conformance tests.
    "junk_identifier:ts": (8, 8),
    # 2026-11-16: 0/1. Miss: `bar: ProgressBar`. Fresh: 10/10, `data1`..`data3`
    # in tests.
    "junk_identifier:rust": (10, 11),
    # 2026-11-16: 0/4. Misses: code examples in `///` and `//!` doc comments.
    # Fresh: 2/2.
    "commented_out_code:rust": (2, 6),
    # 2026-11-16: 0/1. Miss: a `/// <reference types=... />` directive. Fresh:
    # 9/10. Miss: `// dwQuality (-1 == default)`, a field label that parses as a
    # call.
    "commented_out_code:ts": (9, 11),
    # 2026-11-16: 1/1, a bare `# type: ignore`.
    "suppression_without_reason:python": (1, 1),
    # 2026-11-16: 1/1, a crate-wide `#![allow(clippy::...)]`.
    "suppression_without_reason:rust": (1, 1),
    # The 8 TypeScript findings sampled on 2026-11-16 all sat in generated
    # `routeTree.gen.ts` files, and 6 of 8 fresh ones in `__fixtures__/`. Both
    # are now skipped, so they are not counted. Fresh, outside them: 2/2.
    "suppression_without_reason:ts": (2, 2),
}

#: tree-sitter grammar -> the language column its samples belong to
_COLUMN = {"tsx": "ts"}


def _rate(entry: tuple[int, int] | None) -> float | None:
    if not entry or not entry[1]:
        return None
    return entry[0] / entry[1]


def pooled(rule: str) -> tuple[int, int] | None:
    """Every sample for `rule`, across languages and unsplit samples."""
    tp, n = HELDOUT.get(rule, (0, 0))
    for key, (t, k) in HELDOUT_BY_LANG.items():
        if key.split(":", 1)[0] == rule:
            tp, n = tp + t, n + k
    return (tp, n) if n else None


def heldout_precision(rule: str, lang: str | None = None) -> float | None:
    """Language-specific sample wins when it exists."""
    if lang is not None:
        column = _COLUMN.get(lang, lang)
        specific = _rate(HELDOUT_BY_LANG.get(f"{rule}:{column}"))
        if specific is not None:
            return specific
    return _rate(pooled(rule))
