# nits

`nits` is an answer to [`codehygiene`](https://github.com/brianbell-x/codehygiene) ([announcement](https://x.com/brianbellx/status/2121973517273153540)). It describes itself as "a weight-space compiled System One model for code readability and hygiene": 428K ternary parameters, built with no gradients or training, 11 KB on disk, reading source as a grid of bytes. The goal is the same, and the approach is different:

- **It reads syntax, not whitespace.** Findings come from the tree-sitter AST, so re-indenting, swapping tabs or renaming a catch binding moves nothing, across 910 invariance checks. In `codehygiene`, a 5-deep nesting pyramid stops counting as nested after a 1-space re-indent.
- **Every rule publishes its precision, per language.** The number comes from findings read one by one on code the rules were never written against, and the misses stay in the count. `codehygiene` reports false positives on its own benchmark cases, and one threshold applies to every kind of finding.
- **It flags less on real code.** On the same external TypeScript SDK, `nits` reports 0.29 findings per 100 lines against 2.36. It catches `_ = error` and comments that restate the next line, which `codehygiene` misses, and it leaves descriptive names such as `canReusePreviousArticle` alone.
- **Its reward cannot be bought with padding.** In `codehygiene`'s own benchmark, the Rust target grew 56% while its reward rose. `nits.reward` is zero unless the tests pass untouched, and it penalises growth.

It covers 3 languages to `codehygiene`'s 11, and the full comparison is [below](#why-it-works-on-nodes).

`nits` checks the lines a change touched for nine kinds of sloppy code, such as swallowed errors, failures returned as empty values, dev defaults on environment variables, and comments that stand in for code nobody wrote. It parses Python, TypeScript/JavaScript and Rust with tree-sitter, reports AST nodes rather than lines, and is advisory by default.

The one idea that sets it apart is that every rule carries a precision number, and the number is printed next to every finding. That number is the lower of two measurements, taken per language. One comes from a labeled corpus written alongside the rules. The other comes from findings read one by one against the source, on code that was never used while writing the rules. Nine measured cells in the table below fall under the 0.80 bar, and they stay there in plain sight.

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

`nits diff` reads `git diff -U0` and drops every finding outside the added lines. A new file counts as entirely added, and so does an untracked file that Git does not ignore. `--staged` reads the index only, so it leaves untracked files out.

`check` and `diff` exit `0` whatever they find, and `2` when a path or `git diff` fails. With `--gate <precision>` they exit `1` if any finding whose rule measures at least that precision, in that file's language, lands in scope.

```bash
nits diff --gate 0.80          # CI
nits diff --gate 0.90 --json   # for an agent
```

Advisory is the default because a mandatory gate on a probabilistic signal gets switched off, and then it protects nothing.

## The rules

| Rule | Default | ts | py | rs | Held-out sample, true / read | Catches |
|---|---|---|---|---|---|---|
| `config_fallback` | on | 0.95 | 0.95~ | 0.95~ | ts 11/11 | `process.env.X \|\| 'localhost'`, `os.getenv('X', 'changeme')`, `env::var("X").unwrap_or("localhost".into())` |
| `swallowed_error` | on | 0.83 | **0.71** | **0.48** | ts 30/36, py 12/17, rs 14/29 | a handler body with no effect, `_ = error`, `void err`, `r.ok();`, `let _ = send()`, `Err(_) => {}` |
| `error_to_empty_success` | on | 0.95 | 0.95 | **0.00** | ts 29/30, py 8/8, rs 0/8, 4/4 unsplit | a handler returning `{}`, `[]` or `None`, `Err(e) => Vec::new()` |
| `elision_comment` | opt-in | **0.30** | **0.30~** | **0.30~** | ts 3/10 | `// ... rest of the implementation`, `TODO: implement` |
| `deep_nesting` | on | 0.83 | 0.95 | 0.95 | ts 24/29, py 4/4, rs 11/11 | control nesting of depth 5 or more in one function, counted on the AST |
| `narrator_comment` | on | **0.59** | 0.95 | 0.82 | ts 41/69, py 6/6, rs 18/22 | a comment restating the statement it precedes |
| `junk_identifier` | opt-in | **0.47** | **0.62~** | **0.75** | ts 8/17, rs 12/16, 1/1 unsplit | `tmp`, `data1`, `foo`, `_old`, `_backup` |
| `commented_out_code` | opt-in | **0.41** | **0.57~** | **0.60** | ts 9/22, rs 9/15, 7/7 unsplit | a comment whose body parses as a statement |
| `suppression_without_reason` | opt-in | 0.95 | 0.95 | 0.95 | ts 2/2, py 1/1, rs 11/11 | a bare `# noqa` or `@ts-ignore`, a crate-wide `#![allow]` |

A cell without `~` has a held-out sample in that language. A cell with `~` falls back to every sample of that rule pooled together, which is a weaker claim. "Unsplit" samples were recorded without a language and count toward the pooled number only. `src/nits/heldout.py` lists each sample with its seed, the commit it was drawn from, and what each miss was.

A finding counts as true when the code matches what the rule says it reports. Whether acting on it makes the code better is the task metric, which is not measured.

Precision is capped at 0.95. A rule with no observed false positive is not a perfect rule, it is an under-measured one.

Every number is a floor. When a sample showed a bug, I fixed the rule, added a labeled case, and kept the sample's misses in the count. Later samples on code nobody had read add to it. `elision_comment` at 0.30 is the clearest case: all seven misses from `prisma/prisma` were prose such as "same as above", which the rule no longer reports, but the count keeps them until new samples outweigh them.

The cells below 0.80 have causes I can name:

- `swallowed_error` on Python and Rust. In Python, the misses were explanatory comments inside `except` that the rule did not see and fallback assignments read as no-ops, both fixed since. In Rust, `let _ =` discards things that are not errors (`std::mem::replace`, `stack.pop()`) and `Result`s that are dropped on purpose (`try_init()`, `install_default()`), and syntax alone cannot tell those apart from a dropped error.
- `error_to_empty_success` on Rust (0/8). `Err(_) => None` in a parser that returns `Option`, `Err(AlreadyDealtWith) => Vec::new()` after the error was reported, and a `binary_search` miss are all idiomatic. The Rust spelling of this rule does not work, and the TypeScript and Python spellings do.
- `narrator_comment` on TypeScript (0.59). Label comments look like narration: byte-layout notes such as `// Random (5 bytes) -> bytes 4-8`, test-step IDs, field names. On Rust the same rule measures 0.82.
- `junk_identifier` on TypeScript and Rust. `tmp` holding a temporary directory, `thing = sqlDataType('testext/thing')` and SCREAMING_CASE constants such as `EXT_OLD` were all accurate names, and all are exempt now. `allow_deprecated` still matches the `_deprecated` suffix.
- `elision_comment`, above.
- `commented_out_code` (opt-in). Code-shaped lines inside prose comments, such as API translation notes and worked examples, read as dead code. Requiring a whole comment run to be code would drop real cases where one dead line sits under a prose line, so this one is not fixed.

Below-bar cells keep printing, but they are excluded from `--gate 0.80`, from `--min-precision 0.80`, and from the reward, per language. A rule is on by default only if at least one language measures 0.80 or above. `elision_comment` (best cell 0.30) and `junk_identifier` (best cell 0.75) do not, so they run only with `--all`, like `commented_out_code`. `narrator_comment` and `swallowed_error` stay on, although their pooled precision is 0.67 and 0.68, because `narrator_comment` reaches the bar on Python and Rust and `swallowed_error` on TypeScript.

A comment counts as a reason wherever the rules look for one, inside a handler or on the line above a `.catch(() => {})`, `.ok();` or `let _ =`. That is deliberate, and it has a cost: a comment that only narrates, such as `// and clean the FS` above `.ok();`, also silences the finding.

## Why it works on nodes

Findings carry the AST span they came from. Re-indenting a file cannot create or destroy a finding, and a continuation line cannot pass for nesting. A nested function restarts the depth count, so extracting a helper fixes the finding instead of tripping it, and `else if` chains do not accumulate.

Error handlers are matched by shape. `except E: pass`, `except E as e: _ = e` and `catch (err) { void err; }` are the same node shape, so renaming the binding changes nothing. A handler that contains an explanatory comment is not reported, and neither is a `.catch(() => {})` with one on the line above, because silence is the failure mode and a comment is the opposite of silence. A handler returning `false` is not reported either, since a boolean is a decision rather than an empty value.

`codehygiene` is a byte-grid model with hand-tuned thresholds and no parser. It reads whitespace, so its signals move when the indentation does, and they point the wrong way on real code.

| | [`codehygiene` at `423c7e1`](https://github.com/brianbell-x/codehygiene/tree/423c7e1) | `nits` |
|---|---|---|
| Files flagged, on the external TypeScript SDK below | 50% of 298 | not measured |
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

130 cases. 57 are TypeScript, 35 Python and 38 Rust. 14 are adversarial evasions that defeat indentation-based tools. 8 are anti-inversion guards, code where nothing may fire, such as descriptive names.

```
rule                             prec    rec   tp  fp  fn  tn
config_fallback                  1.00   1.00    9   0   0   6
swallowed_error                  1.00   1.00   15   0   0  12
error_to_empty_success           1.00   1.00    9   0   0   5
elision_comment                  1.00   1.00    6   0   0   5
deep_nesting                     1.00   1.00    4   0   0   6
narrator_comment                 1.00   1.00    6   0   0   6
junk_identifier                  1.00   1.00    5   0   0   9
commented_out_code               1.00   1.00    3   0   0   6
suppression_without_reason       1.00   1.00    4   0   0   6

no inversions — every asserted-clean case stayed clean
```

These are in-sample, and therefore worth little on their own. The cases were written with the rules, which is the same trap that makes headline numbers elsewhere unreproducible. `nits eval` fails if any case is misclassified, so a fix cannot quietly trade one case for another.

### Held-out code

These repositories were never read while writing a rule. The table reports finding density with the default rules. Precision comes from the samples in `src/nits/heldout.py`.

| Repository | Language | Files | Lines | Findings | Per 100 lines |
|---|---|---|---|---|---|
| [`jkomyno/esmwell` at `0fe5d07`](https://github.com/jkomyno/esmwell/tree/0fe5d07af718736e5a3cd59b7fd839c268789015) | TS | 113 | 18,555 | 7 | 0.04 |
| [`OrchestratorInc/agent-orchestrator` at `4505071`](https://github.com/OrchestratorInc/agent-orchestrator/tree/450507193ee3988ded574547132c749256ab8454) | TS | 174 | 42,148 | 214 | 0.51 |
| [`jkomyno/uniku` at `66dfbde`](https://github.com/jkomyno/uniku/tree/66dfbdeff0d8a7483beaa98b83c106f6092efb29) | TS | 176 | 22,217 | 21 | 0.09 |
| [`mastra-ai/mastra` at `ff4106b`](https://github.com/mastra-ai/mastra/tree/ff4106b87bef3535162f2d812b6f42a8a9fd1924) | TS | 9,381 | 2,124,326 | 9,350 | 0.44 |
| [`prisma/prisma` at `17385f2`](https://github.com/prisma/prisma/tree/17385f2d86eefa3783e86d94534168a224806c0a) | TS | 7,109 | 1,281,434 | 536 | 0.04 |
| [`prisma/prisma-engines` at `d1b3f37`](https://github.com/prisma/prisma-engines/tree/d1b3f377ca835d73fd50954a5e22128b9ebdd6b8) | Rust | 1,646 | 395,404 | 387 | 0.10 |
| [`mvanhorn/last30days-skill` at `ae8c323`](https://github.com/mvanhorn/last30days-skill/tree/ae8c32327f86f516894a2d94edc6127314465708) | Py | 85 | 36,876 | 145 | 0.39 |
| TypeScript library (external) | TS | 192 | 44,470 | 33 | 0.07 |
| TypeScript SDK (external), 400-file sample | TS | 400 | 85,510 | 244 | 0.29 |
| TypeScript monorepo (external) | TS | 275 | 82,648 | 715 | 0.87 |
| Rust workspace (external) | Rust | 177 | 70,510 | 295 | 0.42 |
| private monorepo | Rust | 317 | 145,375 | 118 | 0.08 |
| private CLI | Rust | 56 | 23,022 | 34 | 0.15 |
| private service | Rust | 27 | 5,449 | 34 | 0.62 |
| private crate | Rust | 51 | 24,708 | 13 | 0.05 |
| private fork of a Rust crate | Rust | 10 | 1,980 | 8 | 0.40 |

The linked rows were measured with the current rules at the commit shown, over the whole tree. So were the TypeScript library and the Rust workspace, from the copies their held-out samples were drawn from. The other rows named by role are private, or were measured on a working tree that no public commit reproduces. They come from an earlier version of the rules, which also ran `elision_comment` and `junk_identifier` by default. The TypeScript SDK row stops at `nits fp`'s default limit of 400 files.

`check` and `fp` skip vendored and generated trees such as `node_modules/`, `third_party/`, `site-packages/`, `dist/`, `target/`, `fixtures/`, `__fixtures__/` and `.worktrees/`, plus minified bundles and `*.gen.*` files. `diff` scores whatever the diff touches.

### Invariance

910 strict checks, 7 meaning-preserving transforms over the 130 cases, all passing.

| Transform | Result |
|---|---|
| `reindent:2`, `reindent:8`, `tabs` | findings identical |
| `trailing_whitespace`, `blank_line_padding` | findings identical |
| `quote_style` | findings identical, and a no-op on Rust, where `'x'` is a char |
| `rename_catch_binding` | findings identical |

Paraphrasing the words in comments changes the findings of 6 of 130 cases (5%), all `narrator_comment`. A rule that compares comment text with identifier text will always move when the text does, so the suite reports this coupling instead of hiding it.

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

The penalty answers a measured failure. In `codehygiene`'s own benchmark, its Rust target grew 56%, from 312 to 488 lines, while its reward rose from 1.0237 to 1.2063.

## Development

```bash
just test        # unit tests
just check       # eval + invariance + tests
just calibrate   # regenerate calibration.py from the labeled corpus
```

To add a rule, implement `rule_x(ctx) -> Iterator[Finding]` in `src/nits/rules.py` and register it. Add labeled cases with positive and negative examples, plus an anti-inversion case if the rule could fire on good code. Then run `just calibrate`, sample it on held-out code with `nits fp <path> --rules rule_x --sample N --seed S`, and record the result, misses included, in `src/nits/heldout.py`.
