# nits

Diff-scoped slop checks with published precision.

`nits` parses code with tree-sitter, reports node-level findings, and refuses to
score lines you did not change. Every rule ships with a precision number, and the
number comes from two places: a labeled corpus, and hand-read samples on code that
was never used while writing the rule. When the two disagree, the lower one wins.

It exists because the obvious approach to this problem — a byte-grid model with
hand-tuned thresholds — measures whitespace and punishes good code. See
[Why not the obvious approach](#why-not-the-obvious-approach).

```console
$ nits diff
src/platform/node.ts
  77:11  p0.95  error_to_empty_success  failure is returned as an empty value — the caller cannot tell this apart from success
        | } catch { return false; }
```

## Install and run

```bash
uv run --project . nits diff            # only lines added by the working tree
uv run --project . nits diff --staged   # only staged lines
uv run --project . nits check src/      # full scan, advisory
uv run --project . nits eval            # per-rule precision/recall + inversions
uv run --project . nits invariance      # meaning-preserving transform suite
uv run --project . nits fp <repo>       # density + sample for hand adjudication
uv run --project . nits rules           # rules and their measured precision
```

Exit codes: `0` always, unless `--gate <precision>` is passed, in which case `1`
if any finding at or above that precision lands in changed lines. Advisory is the
default because a mandatory gate on a probabilistic signal gets switched off, and
then it protects nothing.

```bash
nits diff --gate 0.80          # the CI-shaped invocation
nits diff --gate 0.90 --json   # for agent consumption
```

## The rules

| Rule | Default | Precision | Catches |
|---|---|---|---|
| `config_fallback` | on | 0.95 | `process.env.X \|\| 'localhost'`, `os.getenv('X', 'changeme')`, empty default for a secret |
| `swallowed_error` | on | 0.95 | handler body that has no effect on the error, including `_ = error` and `void err` |
| `error_to_empty_success` | on | 0.95 | handler returning `{}` / `[]` / `None` so callers cannot tell failure from empty |
| `elision_comment` | on | 0.95 | `// ... rest of the implementation`, `TODO: implement` |
| `deep_nesting` | on | 0.95 | control nesting depth ≥ 5 inside one function, counted from the AST |
| `narrator_comment` | on | **0.68** | comment restating the statement it precedes |
| `junk_identifier` | on | 0.95 | `tmp`, `data1`, `foo`, `_old`, `_backup` |
| `commented_out_code` | opt-in | 0.95 | comment whose body parses as a statement |
| `suppression_without_reason` | opt-in | 0.95 | *unscoped* `# noqa` / `@ts-ignore` / `eslint-disable` |

Precision is capped at 0.95: a rule with no observed false positive is not a
perfect rule, it is an under-measured one.

`narrator_comment` is the only rule below the 0.80 bar, and it is the one signal
people actually want. It stays enabled for human reading, is excluded from
`--gate 0.80`, and contributes nothing to the reward. Making it good needs commit
-mined labels, not more heuristics — see [Limits](#what-this-does-not-do).

## Why it is shaped this way

Four decisions, each one a direct answer to a measured failure:

**Nodes, not lines.** Findings carry the AST span they came from. Re-indenting
source cannot create or destroy a finding, and a continuation line cannot
masquerade as nesting. Depth is computed structurally, so a nested function
restarts the count (extracting a helper is the fix, not a breach) and `else if`
chains do not accumulate.

**Handler shape, not handler text.** `except E: pass`,
`except E as e: _ = e` and `catch (err) { void err; }` are the same node shape, so
renaming the binding or re-spelling the statement changes nothing. A handler that
contains an explanatory comment is *not* reported: silence is the failure mode,
and a comment is the opposite of silence. A handler returning `false` from an
`isCaseSensitive()` guard is not reported either: a boolean is a decision, not an
empty value.

**Diff scoping.** Legacy debt is why full-repo gates get turned off. `nits` never
scores a line your change did not touch.

**Published precision, and a lower bound on it.** The labeled corpus was written
alongside the rules, so its precision is in-sample and optimistic — the same trap
that makes other tools' headline numbers unreproducible. `src/nits/heldout.py`
holds hand-adjudicated samples from held-out code, and `precision_of` returns the
lower of the two.

## Measurements

### Labeled corpus

77 cases across 12 languages/rule combinations, including 12 adversarial cases
(the evasions that defeat indentation-based tools) and 5 anti-inversion guards
(descriptive names must not be flagged).

```
rule                             prec    rec   tp  fp  fn  tn
config_fallback                  1.00   1.00    6   0   0   4
swallowed_error                  1.00   1.00    8   0   0   4
error_to_empty_success           1.00   1.00    7   0   0   2
elision_comment                  1.00   1.00    5   0   0   3
deep_nesting                     1.00   1.00    3   0   0   3
narrator_comment                 1.00   1.00    5   0   0   3
junk_identifier                  1.00   1.00    3   0   0   3
commented_out_code               1.00   1.00    2   0   0   3
suppression_without_reason       1.00   1.00    3   0   0   5

no inversions — every asserted-clean case stayed clean
```

In-sample, and therefore worth little on its own.

### Held-out code

`ComposioHQ/composio`, `ts/` package, 400 files / 85,482 lines — never read while
writing a rule:

| | |
|---|---|
| Findings | 247 |
| Density | **0.29 per 100 lines** |
| Hand-read sample (n=36) | **29 true positives (0.81)** |
| `narrator_comment` alone (n=22) | 15 true positives (0.68) |

Also measured, no sampling: `~/work/me` — `esmwell` 0.04/100, a private Rust CLI 0.34,
`agent-orchestrator` 0.57, `durable-streams` 0.87.

### Invariance

539 strict checks (7 meaning-preserving transforms × 77 cases), all passing:

| Transform | Result |
|---|---|
| `reindent` (2 spaces, 8 spaces, tabs) | findings identical |
| `trailing_whitespace`, `blank_line_padding` | findings identical |
| `quote_style` | findings identical |
| `rename_catch_binding` | findings identical |

Paraphrasing comment words moves 5 of 77 cases (6%), all `narrator_comment`. That
coupling is inherent to matching comment text against identifier text, so it is
reported rather than hidden.

## Reward shaping

For RL loops, where a hygiene signal is a reward term:

```python
nits.reward(source, lang="ts", tests_passed=pass_rate, baseline_source=before)
# 0.0 – 1.25
```

Zero when tests fail or when test files were edited. Bonus scales with
precision-weighted finding density, so rules below the precision bar contribute
nothing. **Growth is penalised**: padding cannot buy score, which is the specific
failure that let a benchmark target grow 56% while its "hygiene" rating rose.

## What this does not do

- **No task metric.** Per-rule precision is an intermediate metric. The task
  metric is whether a reviewer accepts the diff faster or an agent stops shipping
  slop, and that needs a human study. This is not a formality: in the closest
  published work, a diagnostic that improved on *every* axis produced the worst
  real outcome, three times over. Precision is the proxy; do not let it become
  the goal. `evals/task_metric.json` is the slot for it, and it is empty.
- **Two languages**, Python and TypeScript/JavaScript. Adding more means adding
  grammar entries, not rule sets.
- **No naming-length rule.** Short is not the same as bad, and length is exactly
  the heuristic that flags `canReusePreviousArticle` while letting
  `const xx = d[47]` through. `junk_identifier` targets placeholder names only.
  Golfed code is under-reported on purpose; `tests/test_rules.py` records that
  gap as a known limit rather than a passing grade.
- **No duplicate-block or type-aware analysis.** Both need cross-file state or a
  type checker, and neither is in a parser.
- **It can be wrong.** Every rule has a measured false-positive rate above zero.
  That is why it is advisory, and why the precision is on every line of output.

## Why not the obvious approach

The closest public artifact in this space is a 13 KB byte-grid "model" that claims
zero tokenizers and zero AST parsers. Measured on the same repository, at
`423c7e1`:

| | byte-grid tool | `nits` |
|---|---|---|
| Files flagged | 50% of 298 | 0.29 findings / 100 lines, diff-scoped |
| Findings / 100 lines | 2.36 | 0.29 |
| 1-space re-indent of a 5-deep pyramid | nesting disappears | unchanged |
| `except Exception as error: _ = error` | not reported | reported |
| `// Loop over the items` before `for item in items` | not reported | reported |
| `const canReusePreviousArticle = …` | reported as poor naming | not reported |
| `const xx = d[47];` | not reported | not reported (known gap) |
| Purpose-built rules | hand-written thresholds tuned until reference repos scored clean | — |

The lesson taken from it is not "do it with a neural net". It is that a hygiene
signal earns trust by publishing what it gets wrong, and that a gate nobody can
afford to turn on is worth nothing.

## Development

```bash
just test        # unit tests
just check       # eval + invariance + tests
just calibrate   # regenerate calibration.py from the labeled corpus
```

Adding a rule: implement `rule_x(ctx) -> Iterator[Finding]` in `src/nits/rules.py`,
register it, add labeled cases with both positive and negative examples, add an
anti-inversion case if the rule could possibly fire on good code, then run
`just calibrate` and sample it on held-out code with `nits fp --sample`.

A rule that cannot reach 0.80 held-out precision ships disabled.
