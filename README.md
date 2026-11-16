# nits

`nits` checks the lines a change touched for nine kinds of sloppy code, such as swallowed errors, failures returned as empty values, dev defaults on environment variables, and comments that stand in for code nobody wrote. It parses Python, TypeScript/JavaScript and Rust with tree-sitter, reports AST nodes rather than lines, and is advisory by default.

The one idea that sets it apart is that every rule carries a precision number, and the number is printed next to every finding. That number is the lower of two measurements, taken per language. One comes from a labeled corpus written alongside the rules. The other comes from findings read by hand on code that was never used while writing them. Two cells in the table below fall under the 0.80 bar, and they stay there in plain sight.

```console
$ nits diff
src/a.ts
      5:5   p0.95  error_to_empty_success       failure is returned as an empty value — the caller cannot tell this apart from success
        | catch {

1 finding(s) across 1 file(s). Advisory: nothing was blocked.
```

## Run it

```bash
uv run --project . nits diff            # working tree against HEAD, staged or not
uv run --project . nits diff --staged   # staged lines only
uv run --project . nits check src/      # full scan
uv run --project . nits rules           # rules and their precision per language
uv run --project . nits eval            # precision and recall on the labeled corpus
uv run --project . nits invariance      # meaning-preserving transform suite
uv run --project . nits fp <path>       # finding density, plus a seeded sample to read by hand
```

`nits diff` reads `git diff -U0` and drops every finding outside the added lines. A new file counts as entirely added, but only once Git knows about it. Untracked files are not in the diff, so they are not checked.

`check` and `diff` exit `0` whatever they find, and `2` when a path or `git diff` fails. With `--gate <precision>` they exit `1` if any finding whose rule measures at least that precision, in that file's language, lands in scope.

```bash
nits diff --gate 0.80          # CI
nits diff --gate 0.90 --json   # for an agent
```

Advisory is the default because a mandatory gate on a probabilistic signal gets switched off, and then it protects nothing.

## The rules

| Rule | Default | ts | py | rs | Read by hand on held-out code | Catches |
|---|---|---|---|---|---|---|
| `config_fallback` | on | 0.95~ | 0.95~ | 0.95~ | none | `process.env.X \|\| 'localhost'`, `os.getenv('X', 'changeme')`, `env::var("X").unwrap_or("localhost".into())` |
| `swallowed_error` | on | 0.95 | 0.95~ | **0.50** | ts 2/2, rs 2/4 | a handler body with no effect, `_ = error`, `void err`, `r.ok();`, `let _ = send()`, `Err(_) => {}` |
| `error_to_empty_success` | on | 0.95~ | 0.95~ | 0.95~ | 4/4 pooled | a handler returning `{}`, `[]` or `None`, `Err(e) => Vec::new()` |
| `elision_comment` | on | 0.95~ | 0.95~ | 0.95~ | none | `// ... rest of the implementation`, `TODO: implement` |
| `deep_nesting` | on | 0.95~ | 0.95~ | 0.95~ | none | control nesting of depth 5 or more in one function, counted on the AST |
| `narrator_comment` | on | **0.64** | 0.76~ | 0.91 | ts 9/14, rs 10/11 | a comment restating the statement it precedes |
| `junk_identifier` | on | 0.95~ | 0.95~ | 0.95~ | 1/1 pooled | `tmp`, `data1`, `foo`, `_old`, `_backup` |
| `commented_out_code` | opt-in | 0.95~ | 0.95~ | 0.95~ | 7/7 pooled | a comment whose body parses as a statement |
| `suppression_without_reason` | opt-in | 0.95~ | 0.95~ | 0.95~ | none | a bare `# noqa` or `@ts-ignore`, a crate-wide `#![allow]` |

A cell without `~` has a hand-read sample in that language. A cell with `~` falls back to the pooled number, which is a weaker claim. Only four cells have their own sample. For the four rules marked "none", the number is the in-sample cap alone, and the held-out side is still unmeasured.

Precision is capped at 0.95. A rule with no observed false positive is not a perfect rule, it is an under-measured one.

The two cells below 0.80 have causes I can name:

- `narrator_comment` on TypeScript (0.64). Type files attract label comments like `// CamelCase delete response schema`, which look like narration unless you know what the next node is. On Rust the same rule measures 0.91, because Rust code tends to comment its own actions.
- `swallowed_error` on Rust (0.50). `let _ = tracing_subscriber::fmt::try_init()` discards a `Result` on purpose, and syntax alone cannot tell that apart from a dropped error. The TypeScript sample measured 2 of 2.

Below-bar cells stay on and keep printing, but they are excluded from `--gate 0.80`, from `--min-precision 0.80`, and from the reward, per language. `narrator_comment` is on by default even though its pooled held-out precision is 0.76. I don't expect more heuristics to fix it on TypeScript. That needs labels mined from real commits.

## Why it works on nodes

Findings carry the AST span they came from. Re-indenting a file cannot create or destroy a finding, and a continuation line cannot pass for nesting. A nested function restarts the depth count, so extracting a helper fixes the finding instead of tripping it, and `else if` chains do not accumulate.

Error handlers are matched by shape. `except E: pass`, `except E as e: _ = e` and `catch (err) { void err; }` are the same node shape, so renaming the binding changes nothing. A handler that contains an explanatory comment is not reported, because silence is the failure mode and a comment is the opposite of silence. A handler returning `false` is not reported either, since a boolean is a decision rather than an empty value.

The alternative I measured against is a byte-grid model with hand-tuned thresholds and no parser. It reads whitespace, so its signals move when the indentation does, and they point the wrong way on real code.

| | byte-grid tool at `423c7e1` | `nits` |
|---|---|---|
| Files flagged on `ComposioHQ/composio` `ts/` | 50% of 298 | not measured |
| Findings per 100 lines, same tree | 2.36 | 0.29 |
| 1-space re-indent of a 5-deep pyramid | nesting disappears | unchanged |
| `except Exception as error: _ = error` | not reported | reported |
| `# Loop over the items` before `for item in items` | not reported | reported |
| `const canReusePreviousArticle = …` | reported as poor naming | not reported |
| `const xx = d[47];` | not reported | not reported, a known gap |
| Languages | 11 | 3 |

The lesson I took from it has nothing to do with neural nets. A hygiene signal earns trust by publishing what it gets wrong, and a gate nobody can afford to turn on is worth nothing.

## Measurements

### Labeled corpus

108 cases. 44 are TypeScript, 33 Python and 31 Rust. 14 are adversarial evasions that defeat indentation-based tools. 8 are anti-inversion guards, code where nothing may fire, such as descriptive names.

```
rule                             prec    rec   tp  fp  fn  tn
config_fallback                  1.00   1.00    9   0   0   6
swallowed_error                  1.00   1.00   13   0   0   6
error_to_empty_success           1.00   1.00    9   0   0   3
elision_comment                  1.00   1.00    6   0   0   3
deep_nesting                     1.00   1.00    4   0   0   5
narrator_comment                 1.00   1.00    6   0   0   5
junk_identifier                  1.00   1.00    4   0   0   4
commented_out_code               1.00   1.00    3   0   0   4
suppression_without_reason       1.00   1.00    4   0   0   6

no inversions — every asserted-clean case stayed clean
```

These are in-sample, and therefore worth little on their own. The cases were written with the rules, which is the same trap that makes headline numbers elsewhere unreproducible.

### Held-out code

These repositories were never read while writing a rule. The table reports finding density only. Precision comes from the hand-read samples in `src/nits/heldout.py`, each with its seed.

| Repository | Language | Files | Lines | Findings | Per 100 lines |
|---|---|---|---|---|---|
| `ComposioHQ/composio` `ts/` | TS | 400 | 85,510 | 244 | 0.29 |
| Rust workspace (external) | Rust | 177 | 70,510 | 332 | 0.47 |
| private monorepo | Rust | 317 | 145,375 | 118 | 0.08 |
| private CLI | Rust | 56 | 23,022 | 34 | 0.15 |
| private service | Rust | 27 | 5,449 | 34 | 0.62 |
| private crate | Rust | 51 | 24,708 | 13 | 0.05 |
| `instant-distance` | Rust | 10 | 1,980 | 8 | 0.40 |
| `esmwell` | Rust/Py | 316 | 52,193 | 23 | 0.04 |
| `agent-orchestrator` | TS | 174 | 42,148 | 240 | 0.57 |
| `durable-streams` | TS | 275 | 82,648 | 715 | 0.87 |

Rows named by role are private or belong to another project.

`check` and `fp` skip vendored and generated trees such as `node_modules/`, `third_party/`, `site-packages/`, `dist/`, `target/` and test fixtures, plus minified bundles. `diff` scores whatever the diff touches.

### Invariance

756 strict checks, 7 meaning-preserving transforms over the 108 cases, all passing.

| Transform | Result |
|---|---|
| `reindent:2`, `reindent:8`, `tabs` | findings identical |
| `trailing_whitespace`, `blank_line_padding` | findings identical |
| `quote_style` | findings identical, and a no-op on Rust, where `'x'` is a char |
| `rename_catch_binding` | findings identical |

Paraphrasing the words in comments changes the findings of 6 of 108 cases (6%), all `narrator_comment`. A rule that compares comment text with identifier text will always move when the text does, so the suite reports this coupling instead of hiding it.

## What it doesn't measure

Per-rule precision is an intermediate metric. The task metric is whether reviewers accept a diff faster, or an agent stops shipping slop, and that needs a human study. `evals/task_metric.json` holds its place, and it is empty. In the closest published work, three times an intermediate metric pointed the opposite way to the task result, and one change that improved every intermediate metric made the model worse. So precision is a proxy here, and the gate should not be tuned against it alone.

There is no naming-length rule. Short is not the same as bad, and length is exactly the heuristic that flags `canReusePreviousArticle` while letting `const xx = d[47]` through. `junk_identifier` targets placeholder names only, so golfed code is under-reported on purpose.

Duplicate blocks and type errors are out of reach. Both need cross-file state or a type checker, and a parser has neither.

A fourth language means grammar entries and a handful of adapter branches, not a new rule set. Its column is worth nothing until it has its own held-out sample.

## Reward shaping

For RL loops, where hygiene is a term in the reward:

```python
nits.reward(source, lang="ts", tests_passed=pass_rate, baseline_source=before)
# 0.0 – 1.25
```

The reward is zero unless every test passes and no test file was edited. A passing solution gets 1.0, plus up to 0.25 as the density of findings at precision 0.80 or above, in that language, falls toward zero. Growth over `baseline_source` costs up to 0.20, and added lines do not dilute the density, so padding cannot buy score. Without `baseline_source` there is no growth penalty.

The penalty answers a measured failure. In the byte-grid tool's own benchmark, its Rust target grew 56%, from 312 to 488 lines, while its reward rose from 1.0237 to 1.2063.

## Development

```bash
just test        # unit tests
just check       # eval + invariance + tests
just calibrate   # regenerate calibration.py from the labeled corpus
```

To add a rule, implement `rule_x(ctx) -> Iterator[Finding]` in `src/nits/rules.py` and register it. Add labeled cases with positive and negative examples, plus an anti-inversion case if the rule could fire on good code. Then run `just calibrate`, sample it on held-out code with `nits fp <path> --sample N --seed S`, and record the result in `src/nits/heldout.py`.
