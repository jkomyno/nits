---
name: nits
description: >-
  Advisory, diff-scoped code-hygiene checks with published per-rule precision.
  Use when reviewing or finishing a change in Python, TypeScript, JavaScript, or Rust,
  to catch swallowed errors, failure-returned-as-empty, dev-config fallbacks,
  truncation comments ("rest of the implementation"), placeholder names, and
  unscoped lint suppressions. Reports only lines the change touched. Advisory:
  do not refactor unrelated code to satisfy it.
---

# nits

Two kinds of finding live here, and they need different treatment:

- **High precision (>= 0.90):** fix them. The pattern is nearly always wrong.
- **Below 0.80 in that language** — every finding prints its precision as `pNN`.
  Today that covers `swallowed_error` on Python and Rust, `narrator_comment` on
  TypeScript, and `error_to_empty_success` on Rust (`nits rules` has the current
  matrix). `elision_comment`, `junk_identifier` and `commented_out_code` are
  below 0.80 in every language and run only with `--all`. Use these as a prompt to read the line, nothing more.
  Do not mechanically delete the comment to make the finding go away; a comment
  that explains *why* is correct and `nits` cannot always tell the difference.

## Run it

```bash
nits diff --json          # working tree against HEAD, staged or not, plus untracked files
nits diff --staged --json # staged changes
```

Findings outside the diff are suppressed by design. If `nits` is not installed:

```bash
uv run --project <path-to-nits> nits diff --json
```

## How to act on each rule

| Rule | What to do |
|---|---|
| `swallowed_error` | Rethrow, handle, or log-and-propagate. Do **not** add a comment to silence the finding; add behaviour. A documented deliberate ignore is already exempt. In Rust this covers `r.ok();`, `let _ = fallible();`, and `Err(_) => {}`. `_ => {}` is exempt on purpose. |
| `error_to_empty_success` | Return a distinguishable failure (tagged error, `Result`, throw). Preserve the caller's ability to tell "empty" from "failed". |
| `config_fallback` | Remove the dev default or fail fast. Never default a secret to `""`, `localhost`, or `changeme`. In Rust that means `unwrap_or`, not `expect("... must be set")`. |
| `elision_comment` | Write the code or delete the line. `// ... rest of the implementation` is a truncation artifact, not documentation. |
| `deep_nesting` | Invert conditions into guard clauses, or extract a helper. Do not re-indent to reduce the count; depth is structural and re-indenting changes nothing. |
| `narrator_comment` | Delete it only if it restates the line below. If it explains why, keep it and move on — on TypeScript treat it (0.59) as "look here". |
| `junk_identifier` | Rename to what the value holds. Do not rename a clear name to a shorter one to satisfy anything. |
| `commented_out_code` | Delete it. Git remembers. |
| `suppression_without_reason` | Name the specific rule or error code, or fix the cause. An unscoped suppression hides errors nobody has read. In Rust only `#![allow(...)]` is reported; item-scoped `#[allow(...)]` already names its lint. |

## Rules of engagement

- **Never rename or restructure code outside the diff.** Findings on untouched
  lines are not yours to fix in this turn.
- **Never add a comment to silence a finding.** That converts a bug into a
  documented bug and makes the next review harder.
- **Never lower `--gate` or pass `--min-precision` to force a clean exit.**
- **In Rust, a name that matches the value is not slop.** `let tmp = tempfile::tempdir()`
  is accurately named, and the rule already exempts it.
  If a finding is a false positive, say so and cite the rule and line.
- **Never pad.** Extracting a helper is fine; splitting statements to change a
  metric is not. The reward penalises growth for this reason.
- **If a finding looks wrong, stop and report it** with the rule id and the source
  line. Precision is published precisely so that disagreements are cheap to
  adjudicate, not so that they can be waved away.

## What it will not tell you

`nits` sees syntax, not intent. It has no type checker and no cross-file state, so
it cannot detect duplicated logic, wrong abstractions, or a well-named function
that does the wrong thing. Passing `nits` is not evidence that a change is good,
only that it is free of nine specific smells. Tests passing and `nits` passing are
two different sentences; do not merge them into one.
