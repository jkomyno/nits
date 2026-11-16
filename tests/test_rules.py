"""Rule behaviour. Each test names the failure it exists to prevent."""

from __future__ import annotations

import pytest

import nits


def rules_fired(source: str, lang: str) -> list[str]:
    return sorted({f.rule for f in nits.check_source(source, lang=lang, include_opt_in=True)})


# --------------------------------------------------------------------------- #
# Evasions that defeat indentation / line-based heuristics
# --------------------------------------------------------------------------- #

def test_one_space_indent_still_reports_nesting():
    """Re-indenting must not hide structure: depth comes from the AST."""
    src = "def f(x):\n if x:\n  if x:\n   if x:\n    if x:\n     if x:\n      return 1\n"
    assert "deep_nesting" in rules_fired(src, "python")


def test_exception_alias_is_not_a_handling_decision():
    """`_ = error` has the same shape as `pass`."""
    src = (
        "def f():\n"
        "    try:\n"
        "        return parse()\n"
        "    except Exception as error:\n"
        "        _ = error\n"
    )
    assert "swallowed_error" in rules_fired(src, "python")


@pytest.mark.parametrize("body", ["return", "return None", "return {}"])
def test_handler_that_returns_nothing_successful(body: str):
    src = f"def f():\n    try:\n        return parse()\n    except Exception:\n        {body}\n"
    assert "error_to_empty_success" in rules_fired(src, "python")


def test_void_binding_is_a_swallow():
    src = "function f() {\n  try { parse(); } catch (err) { void err; }\n}\n"
    assert "swallowed_error" in rules_fired(src, "ts")


def test_identity_catch_is_not_a_swallow():
    """`.catch(e => e)` hands the error back; it does not discard it."""
    src = "const error = await run(tools).catch(e => e);\n"
    assert "swallowed_error" not in rules_fired(src, "ts")


def test_documented_ignore_is_left_alone():
    """Silence is the failure mode. A comment is the opposite of silence."""
    src = (
        "function f(p) {\n"
        "  try {\n"
        "    return walk(p);\n"
        "  } catch {\n"
        "    // Walk toward the filesystem root until an existing directory is found.\n"
        "  }\n"
        "}\n"
    )
    assert "swallowed_error" not in rules_fired(src, "ts")


def test_boolean_return_is_a_decision_not_an_empty_value():
    """`catch { return false }` in an isCaseSensitive() guard is the fail-safe answer."""
    src = (
        "function isCaseSensitive(p) {\n"
        "  try {\n"
        "    return probe(p);\n"
        "  } catch {\n"
        "    return false;\n"
        "  }\n"
        "}\n"
    )
    assert "error_to_empty_success" not in rules_fired(src, "ts")


# --------------------------------------------------------------------------- #
# Anti-inversion: the signals that broke the comparator
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "name",
    [
        "const canReusePreviousArticle = previousGuideMatchesSource;",
        "export type ToolRouterSandboxConfig = z.infer<typeof ToolRouterSandboxConfigSchema>;",
        "const decodeChunkedRealtimeEvent = Schema.decodeUnknownOption(ChunkedRealtimeEvent);",
    ],
)
def test_descriptive_names_are_not_penalised(name: str):
    assert rules_fired(name + "\n", "ts") == []


def test_short_names_are_not_penalised_either():
    """Length is not a proxy for clarity; we refuse to ship that rule."""
    assert "junk_identifier" not in rules_fired("def f(x):\n    return x\n", "python")


def test_golfed_javascript_is_the_junk_zero_width():
    """We under-report golf on purpose; this records the gap, not a passing grade."""
    src = "const xx = d[47];\nconst yy = d[112] + xx;\n"
    fired = rules_fired(src, "ts")
    assert "poor_naming" not in fired


# --------------------------------------------------------------------------- #
# Precision guards
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "src",
    [
        "const region = process.env.AWS_REGION || 'eu-west-1';\n",
        "import os\nport = int(os.getenv('PORT', '8080'))\n",
        "import os\nmode = os.getenv('MODE', 'production')\n",
    ],
)
def test_real_defaults_are_not_fallbacks(src: str):
    lang = "python" if src.startswith("import") else "ts"
    assert "config_fallback" not in rules_fired(src, lang)


@pytest.mark.parametrize(
    "src",
    [
        "const db = process.env.DATABASE_URL || 'localhost';\n",
        "const token = process.env.TOKEN ?? 'changeme';\n",
    ],
)
def test_dev_defaults_are_fallbacks(src: str):
    assert "config_fallback" in rules_fired(src, "ts")


def test_prose_comments_are_not_code():
    assert "commented_out_code" not in rules_fired(
        "// Returns the user id for a session.\nexport const x = 1;\n", "ts"
    )


def test_trailing_comment_on_live_code_is_not_commented_out_code():
    src = "factories = {}  # type: ignore[var-annotated]\n"
    assert "commented_out_code" not in rules_fired(src, "python")


def test_jsdoc_is_documentation_not_narration():
    src = "/** Number of tools synced. */\nclass A {\n  toolsSynced = 0;\n}\n"
    assert "narrator_comment" not in rules_fired(src, "ts")


def test_unscoped_suppression_is_reported_and_scoped_is_not():
    assert "suppression_without_reason" in rules_fired("x = 1  # noqa\n", "python")
    assert "suppression_without_reason" not in rules_fired("x = 1  # noqa: E501\n", "python")


def test_elision_is_reported_with_plural_handlers():
    assert "elision_comment" in rules_fired(
        "// ... rest of the handlers are unchanged\nexport const a = 1;\n", "ts"
    )


# --------------------------------------------------------------------------- #
# Rust: no exceptions, so the same failures have different spellings
# --------------------------------------------------------------------------- #

def test_rust_let_underscore_drops_a_result():
    assert "swallowed_error" in rules_fired("fn f(m: M) {\n    let _ = send(m);\n}\n", "rust")


def test_rust_dot_ok_discards_the_error():
    assert "swallowed_error" in rules_fired("fn f(r: R) {\n    r.ok();\n}\n", "rust")


def test_rust_dot_ok_that_is_used_is_fine():
    src = "fn f(r: R) {\n    if r.ok().is_none() {\n        return;\n    }\n}\n"
    assert "swallowed_error" not in rules_fired(src, "rust")


def test_rust_catch_all_arm_is_not_an_error_swallow():
    """`_ => {}` ignores remaining variants; that is what `_` is for."""
    src = "fn f(r: R) -> i32 {\n    match r {\n        Ok(v) => v,\n        _ => {}\n    }\n}\n"
    assert "swallowed_error" not in rules_fired(src, "rust")


def test_rust_error_arm_that_does_nothing_is_reported():
    src = "fn f(r: R) -> i32 {\n    match r {\n        Ok(v) => v,\n        Err(_) => {}\n    }\n}\n"
    assert "swallowed_error" in rules_fired(src, "rust")


def test_rust_err_arm_returning_an_empty_container():
    src = (
        "fn f(r: R) -> Vec<u8> {\n    match r {\n"
        "        Ok(v) => v,\n        Err(e) => Vec::new()\n    }\n}\n"
    )
    assert "error_to_empty_success" in rules_fired(src, "rust")


def test_rust_env_fallback_defaults():
    bad = 'fn f() {\n    let a = std::env::var("DATABASE_URL").unwrap_or("localhost".into());\n}\n'
    ok = 'fn f() {\n    let a = env::var("AWS_REGION").unwrap_or("eu-west-1".into());\n}\n'
    assert "config_fallback" in rules_fired(bad, "rust")
    assert "config_fallback" not in rules_fired(ok, "rust")


def test_rust_inner_attribute_is_crate_wide():
    assert "suppression_without_reason" in rules_fired("#![allow(dead_code)]\nfn f() {}\n", "rust")
    assert "suppression_without_reason" not in rules_fired(
        "#[allow(dead_code)]\nfn f() {}\n", "rust"
    )


def test_rust_else_if_chain_is_not_nesting():
    src = (
        "fn f(x: i32) -> i32 {\n    if x == 1 {\n        1\n    } else if x == 2 {\n"
        "        2\n    } else if x == 3 {\n        3\n    } else if x == 4 {\n"
        "        4\n    } else {\n        0\n    }\n}\n"
    )
    assert "deep_nesting" not in rules_fired(src, "rust")


def test_temp_names_for_real_temp_files_are_accurate_names():
    src = 'fn f() {\n    let tmp = tempfile::tempdir().expect("tempdir");\n    use_it(tmp);\n}\n'
    assert "junk_identifier" not in rules_fired(src, "rust")


# --------------------------------------------------------------------------- #
# Precision is per language
# --------------------------------------------------------------------------- #

def test_rust_swallow_spelling_is_measured_weaker_than_typescript():
    """The `let _ =` idiom discards Results by design often enough to matter."""
    assert nits.precision_of("swallowed_error", "rust") < 0.80
    assert nits.precision_of("swallowed_error", "ts") >= 0.80


def test_min_precision_uses_the_language_column():
    """Rust `let _ =` measures 0.50; the pooled 0.95 must not let it through a 0.80 filter."""
    src = "fn f(m: M) {\n    let _ = send(m);\n}\n"
    kept = nits.check_source(src, lang="rust", min_precision=0.80)
    assert "swallowed_error" not in {f.rule for f in kept}
    ts = "function f() {\n  try { parse(); } catch (err) { void err; }\n}\n"
    kept = nits.check_source(ts, lang="ts", min_precision=0.80)
    assert "swallowed_error" in {f.rule for f in kept}


def test_rust_narrator_is_measured_stronger_than_typescript():
    """Rust comments its own actions; TypeScript type files attract labels."""
    assert nits.precision_of("narrator_comment", "rust") >= 0.80


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #

def test_unknown_rule_is_an_error():
    with pytest.raises(ValueError, match="unknown rule"):
        nits.check_source("x = 1\n", lang="python", rules=["no_such_rule"])


def test_opt_in_rules_stay_out_of_the_default_set():
    src = "// const user = getUser(id);\nexport const x = 1;\n"
    assert "commented_out_code" not in rules_fired_default(src)
    assert "commented_out_code" in rules_fired(src, "ts")


def rules_fired_default(source: str) -> list[str]:
    return sorted({f.rule for f in nits.check_source(source, lang="ts")})
