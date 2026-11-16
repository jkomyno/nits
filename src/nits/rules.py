"""AST-based rules.

Design constraints, each one a direct answer to a measured failure of
byte-grid heuristics:

* Findings come from *nodes*, so re-indenting source cannot create or destroy a
  finding, and continuation lines cannot masquerade as nesting.
* Catch analysis reads the *body* of the handler. `except E: pass`,
  `except E as e: _ = e`, `catch (e) { void e; }` and `catch (e) { return {} }`
  are all the same shape to the AST, so renaming or re-spelling the binding
  changes nothing.
* Nothing matches on identifier *length*.
* Rules under-flag on purpose where intent is ambiguous (`continue`/`break` in a
  handler, `?? {}` outside a catch). Precision is the product.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterator

from tree_sitter import Node

from .findings import Finding
from .lang import FAMILY, parse, walk


class Ctx:
    """Per-file analysis context.

    tree-sitter's Python bindings build a fresh Node object on every attribute
    access, so the same file walked once per rule costs nine times what walking
    it once costs. Everything below is a cache over a single traversal.
    """

    __slots__ = ("lang", "source", "root", "lines", "_nodes", "_text", "_ident", "_stmt")

    def __init__(self, lang: str, source: bytes, root: Node, lines: list[str]) -> None:
        self.lang = lang
        self.source = source
        self.root = root
        self.lines = lines
        self._nodes: list[Node] | None = None
        self._text: dict[int, str] = {}
        self._ident: dict[int, set[str]] = {}
        self._stmt: dict[int, set[str]] = {}

    @property
    def family(self) -> str:
        return FAMILY[self.lang]

    @property
    def nodes(self) -> list[Node]:
        if self._nodes is None:
            self._nodes = list(walk(self.root))
        return self._nodes

    def text(self, node: Node | None) -> str:
        if node is None:
            return ""
        key = node.id
        cached = self._text.get(key)
        if cached is None:
            cached = self.source[node.start_byte : node.end_byte].decode("utf-8", "replace")
            self._text[key] = cached
        return cached

    def line(self, node: Node) -> int:
        return node.start_point[0] + 1

    def line_text(self, line: int) -> str:
        return self.lines[line - 1] if 0 < line <= len(self.lines) else ""


def make_ctx(source: str | bytes, lang: str) -> Ctx:
    raw = source.encode("utf-8") if isinstance(source, str) else source
    return Ctx(
        lang=lang,
        source=raw,
        root=parse(lang, raw).root_node,
        lines=raw.decode("utf-8", "replace").splitlines(),
    )


def _find(ctx: Ctx, rule: str, node: Node, message: str) -> Finding:
    return Finding(
        rule=rule,
        message=message,
        line=ctx.line(node),
        col=node.start_point[1] + 1,
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1] + 1,
        evidence=ctx.text(node).strip().splitlines()[0][:160] if ctx.text(node).strip() else "",
    )


# --------------------------------------------------------------------------- #
# Shared node predicates
# --------------------------------------------------------------------------- #

_SUSPICIOUS_DEFAULTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "dev-secret", "dev_secret", "devsecret", "dev-secrets",
    "changeme", "change-me", "change_me",
    "secret", "password", "passwd", "pwd", "admin", "root",
    "test", "testing", "dev", "development", "local",
    "dummy", "example", "example.com", "foo", "bar", "baz",
    "x", "xx", "xxx", "abc", "todo", "skip",
}
_EMPTY_DEFAULTS = {"", " "}
_SECRETISH = re.compile(r"(KEY|TOKEN|SECRET|PASS|PWD|CRED|AUTH|SALT|SIGNATURE)", re.I)

MAX_DEPTH = 5

#: Rust has no `comment` node: it distinguishes line and block comments.
_COMMENT_TYPES = frozenset({"comment", "line_comment", "block_comment"})


def _is_comment(node: Node) -> bool:
    return node.type in _COMMENT_TYPES

_JUNK_EXACT = {
    "tmp", "temp", "tmp1", "temp1", "foo", "bar", "baz", "asdf",
    "qwerty", "dummy", "stuff", "thing", "things", "var1", "var2", "obj1", "res1",
}
_JUNK_PATTERN = re.compile(
    r"^(?:tmp|temp|data|test|foo|bar|baz|var|obj|res)\d+$"
    r"|_(?:old|bak|backup|copy|orig|original|deprecated|depr|temp|tmp|unused|wip)$",
    re.I,
)

_CONTROL_JS = {
    "if_statement", "for_statement", "for_in_statement", "while_statement",
    "do_statement", "switch_statement", "try_statement",
}
_CONTROL_PY = {
    "if_statement", "for_statement", "while_statement", "try_statement",
}
_CONTROL_RUST = {
    "if_expression", "for_expression", "while_expression", "loop_expression",
    "match_expression",
}
_FUNC_NODES = {
    "function_definition", "function_declaration", "method_definition",
    "arrow_function", "function_expression", "generator_function_declaration",
    "function_item", "closure_expression",
}


def _string_literal(node: Node | None) -> str | None:
    """Return the decoded value of a string literal node, else None."""
    if node is None:
        return None
    if node.type in ("string", "template_string", "string_literal"):
        raw = node.text.decode("utf-8", "replace") if node.text else ""
        if node.type == "template_string" and raw.startswith("`") and "${" in raw:
            return None
        return raw.strip("'\"`")
    if node.type in ("string_fragment",):
        return (node.text or b"").decode("utf-8", "replace")
    return None


def _first_string_in(node: Node, ctx: Ctx) -> str | None:
    """First string literal in a subtree, unwrapping `.into()` / closures."""
    for cand in walk(node):
        value = _string_literal(cand)
        if value is not None:
            return value
    return None


def _bool_op(node: Node) -> str | None:
    """Return the operator text for a boolean/binary junction node."""
    if node.type in ("boolean_operator", "binary_expression", "ternary_expression"):
        for child in node.children:
            if not child.is_named:
                return child.type
    return None


_PY_ENV_CALLS = {"os.getenv", "os.environ.get", "environ.get", "getenv"}
_PY_ENV_OBJECTS = {"os.environ", "environ"}


_CONTROLS = {"python": _CONTROL_PY, "js": _CONTROL_JS, "rust": _CONTROL_RUST}

_RUST_ENV_CALLS = {"env::var", "env::var_os", "std::env::var", "std::env::var_os"}
_RUST_DEFAULTS = {"unwrap_or", "unwrap_or_else", "unwrap_or_default", "unwrap_or_else_default"}


def _env_key(node: Node, ctx: Ctx) -> str | None:
    """Return the environment-variable name read by `node`, if it reads one."""
    if ctx.family == "python":
        return _py_env_key(node, ctx)
    if ctx.family == "rust":
        return _rust_env_key(node, ctx)
    return _js_env_key(node, ctx)


def _rust_env_key(node: Node, ctx: Ctx) -> str | None:
    """`env::var("X")` inside `...unwrap_or(...)`."""
    if node.type != "call_expression":
        return None
    if ctx.text(node.child_by_field_name("function")) not in _RUST_ENV_CALLS:
        return None
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for arg in args.named_children:
        return _string_literal(arg)
    return None


def _rust_env_default(node: Node, ctx: Ctx) -> tuple[bool, str]:
    """(found, default) for a `Result`/`Option` fallback wrapping an env read."""
    parent = node.parent
    if parent is None or parent.type != "field_expression":
        return False, ""
    field_node = parent.child_by_field_name("field")
    field = ctx.text(field_node) if field_node is not None else ""
    if field not in _RUST_DEFAULTS:
        return False, ""
    call = parent.parent
    if call is None or call.type != "call_expression":
        return False, ""
    if field == "unwrap_or_default":
        return True, ""  # the type's default, usually the empty string
    args = call.child_by_field_name("arguments")
    if args is None:
        return False, ""
    found = _first_string_in(args, ctx)
    return (True, found) if found is not None else (False, "")


def _py_env_key(node: Node, ctx: Ctx) -> str | None:
    if node.type == "call":
        if ctx.text(node.child_by_field_name("function")) not in _PY_ENV_CALLS:
            return None
        args = node.child_by_field_name("arguments")
        named = args.named_children if args is not None else []
        return _string_literal(named[0]) if named else None
    if node.type != "subscript":
        return None
    if ctx.text(node.child_by_field_name("value")) not in _PY_ENV_OBJECTS:
        return None
    return _string_literal(node.child_by_field_name("subscript"))


def _js_env_key(node: Node, ctx: Ctx) -> str | None:
    if node.type == "member_expression":
        if ctx.text(node.child_by_field_name("object")) != "process.env":
            return None
        return ctx.text(node.child_by_field_name("property")) or None
    if node.type != "subscript_expression":
        return None
    if ctx.text(node.child_by_field_name("object")) != "process.env":
        return None
    return _string_literal(node.child_by_field_name("index"))


def _inert_statements(nodes: list[Node], ctx: Ctx) -> bool:
    """True if every statement is a no-op on the error (pass / void / alias)."""
    if not nodes:
        return True
    for stmt in nodes:
        t = stmt.type
        if t in ("pass_statement", "empty_statement") or _is_comment(stmt):
            continue
        if t == "expression_statement":
            inner = [c for c in stmt.named_children if not _is_comment(c)]
            if not inner:
                continue
            expr = inner[0]
            if expr.type in ("identifier", "member_expression", "attribute", "subscript"):
                continue
            if expr.type == "unary_expression" and ctx.text(expr).startswith("void"):
                continue
            # python nests assignments inside expression_statement
            if expr.type in ("assignment", "augmented_assignment"):
                rhs = expr.child_by_field_name("right")
                if rhs is not None and rhs.type in (
                    "identifier", "member_expression", "attribute", "subscript",
                ):
                    continue
            return False
        if t == "assignment" or t == "assignment_expression":
            rhs = stmt.child_by_field_name("right")
            if rhs is not None and rhs.type in (
                "identifier", "member_expression", "attribute", "subscript",
            ):
                continue
            return False
        if t in ("lexical_declaration", "variable_declaration"):
            decls = [c for c in stmt.named_children if c.type == "variable_declarator"]
            inits = [d.child_by_field_name("value") for d in decls]
            if inits and all(i is None or i.type in ("identifier", "member_expression") for i in inits):
                continue
            return False
        return False
    return True



def _empty_return_value(node: Node, ctx: Ctx) -> bool:
    """True if a return statement hands back an empty/null-ish success value."""
    if node.type != "return_statement":
        return False
    named = [c for c in node.named_children if not _is_comment(c)]
    if not named:
        return True  # bare `return` -> returns None
    val = named[0]
    # A boolean is a decision, not an empty value: `catch { return false }` in an
    # `isCaseSensitive()` guard is the fail-safe answer, not a hidden failure.
    if val.type in ("none", "null", "undefined"):
        return True
    if val.type in ("object", "dictionary"):
        return len([c for c in val.named_children if c.type == "pair"]) == 0
    if val.type in ("array", "list", "tuple"):
        return len([c for c in val.named_children if not _is_comment(c)]) == 0
    return False


def _catch_bodies(node: Node, ctx: Ctx) -> Iterator[tuple[Node, Node]]:
    """Yield (header_node, body_node) for every catch/except handler."""
    if ctx.family == "python":
        yield from _py_catch_bodies(node)
        return
    yield from _js_catch_bodies(node, ctx)


def _py_catch_bodies(node: Node) -> Iterator[tuple[Node, Node]]:
    if node.type != "except_clause":
        return
    for child in node.named_children:
        if child.type == "block":
            yield node, child


def _js_catch_bodies(node: Node, ctx: Ctx) -> Iterator[tuple[Node, Node]]:
    if node.type == "catch_clause":
        for child in node.named_children:
            if child.type == "statement_block":
                yield node, child
        return
    if node.type != "call_expression":
        return
    fn = node.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return
    if ctx.text(fn.child_by_field_name("property")) != "catch":
        return
    args = node.child_by_field_name("arguments")
    if args is None:
        return
    for arg in args.named_children:
        if arg.type not in ("arrow_function", "function_expression"):
            continue
        body = arg.child_by_field_name("body")
        if body is not None:
            yield node, body


# --------------------------------------------------------------------------- #
# Rule: config_fallback
# --------------------------------------------------------------------------- #

def rule_config_fallback(ctx: Ctx) -> Iterator[Finding]:
    """`process.env.X || 'localhost'` / `os.getenv('X', 'changeme')`."""
    for node in ctx.nodes:
        key = _env_key(node, ctx)
        if not key:
            continue
        default: str | None = None
        # a) junction: env || 'x', env ?? 'x', env or 'x'
        if node.parent is not None:
            p = node.parent
            if p.type in ("boolean_operator", "binary_expression") and _bool_op(p) in ("||", "??", "or", "&&"):
                other = p.child_by_field_name("right")
                if other is not None:
                    default = _string_literal(other)
        # b) call with a default argument: os.getenv('X', 'y')
        if default is None and node.type == "call":
            args = node.child_by_field_name("arguments")
            if args:
                named = [c for c in args.named_children if not _is_comment(c)]
                if len(named) >= 2:
                    default = _string_literal(named[1])
        # c) rust: env::var("X").unwrap_or("y") / unwrap_or_default()
        if default is None and ctx.family == "rust":
            found, value = _rust_env_default(node, ctx)
            if found:
                default = value
        if default is None:
            continue
        low = default.strip().lower()
        bad = low in _SUSPICIOUS_DEFAULTS or (
            default in _EMPTY_DEFAULTS and _SECRETISH.search(key) is not None
        )
        if not bad:
            continue
        yield _find(
            ctx, "config_fallback", node,
            f'{key} silently falls back to "{default}" — fail fast, or make the default explicit and env-scoped',
        )


# --------------------------------------------------------------------------- #
# Rule: swallowed_error
# --------------------------------------------------------------------------- #

_NON_BLOCK_NOOP = {"undefined", "null", "None", "{}", "[]", "void 0", ""}


def _block_children(body: Node) -> list[Node] | None:
    """Statements of a handler body, or None for a non-block expression body."""
    if body.type in ("statement_block", "block"):
        return [c for c in body.named_children if not _is_comment(c)]
    return None


def _documented(body: Node, ctx: Ctx, min_chars: int = 3) -> bool:
    """True when a handler explains itself in a comment.

    `catch {}` and `catch { /* keep walking to the filesystem root */ }` are not
    the same act. The second one told you why. Silence is the failure mode, and
    a comment is the opposite of silence. The marker alone does not count: an
    empty `//` is silence with punctuation.
    """
    candidates = [n for n in walk(body) if _is_comment(n)]
    # Python attaches a leading comment to the `except_clause`, not its block.
    if body.parent is not None and body.parent.type == "except_clause":
        candidates += [c for c in body.parent.named_children if _is_comment(c)]
    for child in candidates:
        text = _COMMENT_MARK.sub("", ctx.text(child)).strip().rstrip("*/").strip()
        if len(text) >= min_chars:
            return True
    return False


_RUST_DEFAULT_BODIES = re.compile(
    r"^(?:Vec::new\(\)|String::new\(\)|HashMap::new\(\)|BTreeMap::new\(\)"
    r"|Default::default\(\)|None|vec!\[\]|String::from\(\"\"\))$"
)


def _block_is_empty(block: Node, ctx: Ctx) -> bool:
    named = [c for c in block.named_children if not _is_comment(c)]
    return not named and not _documented(block, ctx)


def _rust_swallowed(ctx: Ctx) -> Iterator[Finding]:
    """Rust has no exceptions, so discarding an error has four spellings."""
    for node in ctx.nodes:
        if node.type == "expression_statement":
            inner = [c for c in node.named_children if not _is_comment(c)]
            if not inner or inner[0].type != "call_expression":
                continue
            fn = inner[0].child_by_field_name("function")
            if fn is None or fn.type != "field_expression":
                continue
            if ctx.text(fn.child_by_field_name("field")) != "ok":
                continue
            yield _find(
                ctx, "swallowed_error", node,
                "`.ok()` turns the failure into `None` and drops it — handle it or propagate",
            )
        elif node.type == "let_declaration":
            pattern = node.child_by_field_name("pattern")
            value = node.child_by_field_name("value")
            if pattern is None or pattern.type != "_" or value is None:
                continue
            if value.type not in (
                "call_expression", "macro_invocation", "await_expression", "try_expression",
            ):
                continue
            yield _find(
                ctx, "swallowed_error", node,
                "`let _ =` drops the result — a failing `Result` becomes silence",
            )
        elif node.type == "match_arm":
            pattern = node.child_by_field_name("pattern")
            value = node.child_by_field_name("value")
            if pattern is None or value is None or value.type != "block":
                continue
            # `_ => {}` ignores the remaining variants, which is what `_` is for.
            if not ctx.text(pattern).strip().startswith("Err"):
                continue
            if _block_is_empty(value, ctx):
                yield _find(
                    ctx, "swallowed_error", pattern,
                    "match arm does nothing — handle the case, propagate, or say why",
                )
        elif node.type == "if_expression":
            condition = node.child_by_field_name("condition")
            consequence = node.child_by_field_name("consequence")
            if condition is None or condition.type != "let_condition":
                continue
            if consequence is None or not _block_is_empty(consequence, ctx):
                continue
            yield _find(
                ctx, "swallowed_error", node,
                "`if let` with an empty body ignores the case — handle it or say why",
            )


_STATEMENT_CONTAINERS = frozenset({"statement_block", "program", "switch_case", "switch_default"})


def _explained_above(node: Node, ctx: Ctx) -> bool:
    """True when a comment directly above the statement says why.

    `.catch(() => {})` has no body to hold a comment, so its reason goes on the
    line above. A run of such lines can share one comment.
    """
    stmt = node
    while stmt.parent is not None and stmt.parent.type not in _STATEMENT_CONTAINERS:
        stmt = stmt.parent
    row = stmt.start_point[0]
    prev = stmt.prev_named_sibling
    while prev is not None and prev.end_point[0] == row - 1:
        if _is_comment(prev):
            text = _COMMENT_MARK.sub("", ctx.text(prev)).strip().rstrip("*/").strip()
            return len(text) >= 3
        if ".catch(" not in ctx.text(prev):
            return False
        row = prev.start_point[0]
        prev = prev.prev_named_sibling
    return False


def rule_swallowed_error(ctx: Ctx) -> Iterator[Finding]:
    """A handler whose body has no effect on the error."""
    if ctx.family == "rust":
        yield from _rust_swallowed(ctx)
        return
    for node in ctx.nodes:
        for header, body in _catch_bodies(node, ctx):
            stmts = _block_children(body)
            if stmts is None:
                # `.catch(e => e)` returns the error to the caller: an identity
                # catch, not a swallowed one.
                if ctx.text(body).strip() in _NON_BLOCK_NOOP and not _explained_above(node, ctx):
                    yield _find(
                        ctx, "swallowed_error", header,
                        "error is caught and discarded — rethrow, handle it, or let it propagate",
                    )
                continue
            if _documented(body, ctx):
                continue
            if any(s.type == "return_statement" for s in stmts):
                continue  # error_to_empty_success owns returns
            if _inert_statements(stmts, ctx):
                yield _find(
                    ctx, "swallowed_error", header,
                    "error is caught and discarded — rethrow, handle it, or let it propagate",
                )


# --------------------------------------------------------------------------- #
# Rule: error_to_empty_success
# --------------------------------------------------------------------------- #

def _rust_empty_success(ctx: Ctx) -> Iterator[Finding]:
    """`Err(e) => Vec::new()` makes a failure indistinguishable from an empty result."""
    for node in ctx.nodes:
        if node.type != "match_expression":
            continue
        for arm in walk(node):
            if arm.type != "match_arm":
                continue
            pattern = arm.child_by_field_name("pattern")
            value = arm.child_by_field_name("value")
            if pattern is None or value is None:
                continue
            if not ctx.text(pattern).strip().startswith("Err"):
                continue
            body = ctx.text(value).strip()
            if value.type == "block":
                inner = [c for c in value.named_children if not _is_comment(c)]
                if len(inner) != 1:
                    continue
                body = ctx.text(inner[0]).strip()
            if _RUST_DEFAULT_BODIES.match(body):
                yield _find(
                    ctx, "error_to_empty_success", arm,
                    f"`Err` arm returns `{body}` — the caller cannot tell failure from an empty result",
                )
                break


def rule_error_to_empty_success(ctx: Ctx) -> Iterator[Finding]:
    """A handler that converts failure into an empty-looking success value."""
    if ctx.family == "rust":
        yield from _rust_empty_success(ctx)
        return
    for node in ctx.nodes:
        for header, body in _catch_bodies(node, ctx):
            if _block_children(body) is None:
                continue  # expression bodies are handled as identity catches
            if _documented(body, ctx):
                continue
            stmts = _block_children(body) or []
            rets = [s for s in stmts if s.type == "return_statement"]
            if not rets:
                continue
            if all(_empty_return_value(r, ctx) for r in rets) and _inert_statements(
                [s for s in stmts if s.type != "return_statement"], ctx
            ):
                yield _find(
                    ctx, "error_to_empty_success", header,
                    "failure is returned as an empty value — the caller cannot tell this apart from success",
                )


# --------------------------------------------------------------------------- #
# Rule: suppression_without_reason
# --------------------------------------------------------------------------- #

_SUPPRESSION_JS = re.compile(r"@ts-ignore\b|eslint-disable(?:-next-line|-line)?\b|biome-ignore\b|@ts-nocheck\b")
_SUPPRESSION_PY = re.compile(r"#\s*noqa\b|#\s*type:\s*ignore\b|#\s*pyright:\s*ignore\b|#\s*pylint:\s*disable")
_REASON_PY = re.compile(r"#\s*\S|--\s*\S")


def _rust_suppressions(ctx: Ctx) -> Iterator[Finding]:
    """`#![allow(...)]` applies to the whole crate or module; `#[allow(...)]` does not."""
    for node in ctx.nodes:
        if node.type != "inner_attribute_item":
            continue
        if "allow" not in ctx.text(node):
            continue
        yield _find(
            ctx, "suppression_without_reason", node,
            "crate/module-wide `#![allow]` — scope it to the item that needs it",
        )


def rule_suppression_without_reason(ctx: Ctx) -> Iterator[Finding]:
    """*Unscoped* lint/type suppressions.

    `# noqa: F401` names what it silences — the code is the reason. `# noqa`
    with nothing after it silences everything on the line, including errors the
    author has never seen. Only the second is a risk worth reporting; flagging
    the first is a policy opinion, and policy opinions at that volume are how
    linters get switched off.
    """
    if ctx.family == "rust":
        yield from _rust_suppressions(ctx)
        return
    for node in ctx.nodes:
        if not _is_comment(node):
            continue
        text = ctx.text(node)
        if ctx.family == "js":
            m = _SUPPRESSION_JS.search(text)
            if not m:
                continue
            if "@ts-expect-error" in text:
                continue  # asserts an error exists; that is the good form
            tail = text[m.end() :].strip()
            scoped = bool(re.match(r"^[\w@./-]+", tail))
            if scoped:
                continue
            if tail.startswith("--") and tail[2:].strip():
                continue
        else:
            m = _SUPPRESSION_PY.search(text)
            if not m:
                continue
            tail = text[m.end() :].strip()
            # `: E501`, `[misc]`, `=protected-access` all name the target
            if re.match(r"^[:=]\s*\S", tail) or re.match(r"^\[\w", tail):
                continue
            if tail.startswith("#") and tail[1:].strip():
                continue
            if tail.startswith("--") and tail[2:].strip():
                continue
        yield _find(
            ctx, "suppression_without_reason", node,
            "unscoped suppression — name the rule, or fix the underlying issue",
        )


# --------------------------------------------------------------------------- #
# Rule: elision_comment
# --------------------------------------------------------------------------- #

_ELISION = re.compile(
    r"\b(?:rest|remainder) of (?:the |this |your )?(?:implementation|code|file|methods?|functions?|logic|handlers?)\b"
    r"|\bfor brevity\b|\bomitted for (?:brevity|clarity|space|demo|readability)\b"
    r"|\belided\b"
    r"|\bimplementation details (?:omitted|elided|follow)\b"
    r"|\bunchanged (?:from|code|implementation|aside)\b"
    r"|\bexisting (?:code|implementation|logic) (?:remains|unchanged|as-is)\b"
    r"|\b(?:same|identical) as (?:above|before)\b"
    r"|\btodo:? implement\b|\bnot implemented yet\b|\bplaceholder (?:for|implementation)\b"
    r"|^\s*\.\.\.\s*$|^\s*snip\s*$",
    re.I,
)


def rule_elision_comment(ctx: Ctx) -> Iterator[Finding]:
    """Comments that stand in for code that was never written."""
    for node in ctx.nodes:
        if not _is_comment(node):
            continue
        text = ctx.text(node)
        body = re.sub(r"^\s*(?://+|/\*+|\*+|#+|--+)+", "", text).strip().rstrip("*/").strip()
        if _ELISION.search(body):
            yield _find(
                ctx, "elision_comment", node,
                "elision comment — implement it or delete the line; this is a truncation artifact",
            )


# --------------------------------------------------------------------------- #
# Rule: narrator_comment
# --------------------------------------------------------------------------- #

_STOP = {
    "the", "and", "for", "with", "this", "that", "from", "into", "then", "than",
    "here", "there", "when", "what", "will", "have", "been",
}
_KEYWORD_TOKENS = {
    "return_statement": {"return"},
    "if_statement": {"if", "check"},
    "for_statement": {"for", "loop", "iterate", "each"},
    "for_in_statement": {"for", "loop", "iterate", "each"},
    "while_statement": {"while", "loop"},
    "try_statement": {"try"},
    "throw_statement": {"throw"},
    "raise_statement": {"raise", "throw"},
    "return_expression": {"return"},
    "if_expression": {"if", "check"},
    "for_expression": {"for", "loop", "iterate", "each"},
    "while_expression": {"while", "loop"},
    "match_expression": {"match"},
}
_ASSIGN_TOKENS = {"set", "assign", "store", "save", "put", "update"}


def _words(text: str) -> set[str]:
    out: set[str] = set()
    # split camelCase, snake_case, kebab
    for raw in re.split(r"[^A-Za-z0-9]+", text):
        if not raw:
            continue
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", raw) or [raw]
        for part in parts:
            w = part.lower()
            if len(w) < 4 or w in _STOP:
                continue
            out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


#: Naming a type is a label, not a restatement of an action.
_TYPE_DECLS = frozenset({
    "type_alias_declaration", "interface_declaration", "enum_declaration",
    "type_item", "struct_item", "trait_item", "enum_item", "union_item",
    "type_alias",
})

_ASSERTION_COMMENT = re.compile(
    r"^\W*(?:should|expect|expects|expected|assert|asserts|verify|verifies|ensures?|must|never|always|note|warning|caveat)\b",
    re.I,
)


def _identifier_words(node: Node, ctx: Ctx) -> set[str]:
    """Identifier vocabulary of a statement, pruning type annotations.

    Recursive on purpose: a flat walk cannot prune a subtree, and skipping a
    node without pruning is how a `list[ToolParam]` annotation ended up matching
    a comment about types.
    """
    cached = ctx._ident.get(node.id)
    if cached is not None:
        return cached
    words: set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        parent = cur.parent
        if parent is not None and parent.child_by_field_name("type") is cur:
            continue  # prune: type vocabulary is not code vocabulary
        if cur.type in ("identifier", "property_identifier", "field_identifier"):
            words |= _words(cur.text.decode("utf-8", "replace"))
        stack.extend(cur.named_children)
    ctx._ident[node.id] = words
    return words


def _statement_words(stmt: Node, ctx: Ctx) -> set[str]:
    cached = ctx._stmt.get(stmt.id)
    if cached is not None:
        return cached
    words = _identifier_words(stmt, ctx)
    for node in walk(stmt):
        for key, extra in _KEYWORD_TOKENS.items():
            if node.type == key:
                words |= extra
        if node.type in ("assignment", "assignment_expression", "augmented_assignment"):
            words |= _ASSIGN_TOKENS
    ctx._stmt[stmt.id] = words
    return words


_BODY_NODES = {
    "block", "statement_block", "class_body", "declaration_list",
    "field_declaration_list", "enum_variant_list",
}
_DEFINITION_NODES = _FUNC_NODES | {
    "class_definition", "class_declaration", "impl_item", "mod_item",
    "struct_item", "trait_item", "enum_item", "function_item",
}


def _next_statement(children, start: int, ctx: Ctx) -> Node | None:
    """The statement a comment run documents.

    Python attaches a suite's leading comment to the enclosing definition rather
    than to the block, so the next sibling is a `block` and the statement is its
    first child.
    """
    for sib in children[start:]:
        if _is_comment(sib):
            continue
        if sib.type in _BODY_NODES:
            inner = [c for c in sib.named_children if not _is_comment(c)]
            return inner[0] if inner else None
        if sib.type in _DEFINITION_NODES:
            body = sib.child_by_field_name("body")
            if body is not None:
                inner = [c for c in body.named_children if not _is_comment(c)]
                return inner[0] if inner else None
        return sib
    return None


_DOC_OR_DIRECTIVE = re.compile(
    r"^\s*(?:/\*\*|///|//!|\*)"
    r"|@ts-(?:ignore|nocheck|expect-error)|eslint|biome-ignore|prettier-ignore|istanbul|c8 ignore"
    r"|webpackChunkName|#\s*type:|noqa|pylint:|pyright:"
)


_SECTION_RULE = re.compile(r"^[\s*#/\-=_`~|+]{0,3}(?:-{3,}|={3,}|_{3,}|\*{3,}|#{3,})")


def _is_doc_or_directive(text: str) -> bool:
    """Doc comments document; directive comments instruct tooling. Neither narrates."""
    return bool(_DOC_OR_DIRECTIVE.search(text)) or "\n" in text


_BOX_RULE = re.compile(r"[─━═╌┄]{3,}")


def _is_section_rule(text: str) -> bool:
    """`// --- Session schemas ---` is a structural header, not narration."""
    return bool(_SECTION_RULE.search(text.strip()) or _BOX_RULE.search(text))


def rule_narrator_comment(ctx: Ctx) -> Iterator[Finding]:
    """A comment that restates the statement it precedes.

    Uses AST identifiers, not 4-gram sketches: overlap is computed against the
    names the *next statement* actually binds or reads.
    """
    for parent in ctx.nodes:
        children = parent.children
        for i, child in enumerate(children):
            if not _is_comment(child):
                continue
            # Only evaluate at the start of a comment run. Mid-run fragments
            # ("... and it returns the filePath.") are prose, not narration.
            if i > 0 and _is_comment(children[i - 1]):
                continue
            # collect a run of comments (comments are `extra` nodes, so they are
            # siblings of the statement they document)
            run = []
            j = i
            while j < len(children) and _is_comment(children[j]):
                run.append(children[j])
                j += 1
            stmt = _next_statement(children, j, ctx)
            if stmt is None or stmt.type in _TYPE_DECLS:
                continue
            comment_words = _words(" ".join(ctx.text(c) for c in run))
            if len(comment_words) < 1:
                continue
            # A paragraph explains; a one-line comment narrates. Only the latter
            # is the failure mode, so long prose blocks are left alone.
            if len(run) > 2:
                continue
            # "Should remove the entry" asserts behaviour, it does not restate code.
            if _ASSERTION_COMMENT.match(ctx.text(run[0])):
                continue
            if any(_is_doc_or_directive(ctx.text(c)) for c in run):
                continue
            if any(_is_section_rule(ctx.text(c)) for c in run):
                continue
            stmt_words = _statement_words(stmt, ctx)
            ident_words = _identifier_words(stmt, ctx)
            overlap = comment_words & stmt_words
            if not overlap:
                continue
            # Keyword overlap alone (`return`/`loop`) is not evidence; a real
            # restatement shares a name.
            if not (overlap & ident_words):
                continue
            ratio = len(overlap) / len(comment_words)
            if len(overlap) >= 2 and ratio >= 0.5:
                yield _find(
                    ctx, "narrator_comment", run[0],
                    f"comment restates the next statement ({', '.join(sorted(overlap))}) — drop it or say why",
                )


# --------------------------------------------------------------------------- #
# Rule: deep_nesting
# --------------------------------------------------------------------------- #

def rule_deep_nesting(ctx: Ctx) -> Iterator[Finding]:
    """Report one finding per function body whose control nesting exceeds the limit.

    Depth is structural. A nested function restarts the count (extracting a
    helper is the fix, not a breach), and `else if` chains do not accumulate.
    """
    controls = _CONTROLS[ctx.family]
    limit = MAX_DEPTH

    def depth_of(node: Node, depth: int, worst: list[tuple[int, Node]]) -> None:
        for child in node.named_children:
            if child.type in _FUNC_NODES:
                continue  # a new scope owns its own depth
            if child.type in controls:
                if family_is_else_if(child, ctx):
                    # `else if` continues the chain at the same level
                    depth_of(child, depth, worst)
                    continue
                d = depth + 1
                if d > worst[0][0]:
                    worst[0] = (d, child)
                depth_of(child, d, worst)
                continue
            depth_of(child, depth, worst)

    for node in ctx.nodes:
        if node.type not in _FUNC_NODES:
            continue
        worst: list[tuple[int, Node]] = [(0, node)]
        depth_of(node, 0, worst)
        if worst[0][0] >= limit:
            yield _find(
                ctx, "deep_nesting", worst[0][1],
                f"control nesting depth {worst[0][0]} — invert conditions into guard clauses or extract a helper",
            )


def family_is_else_if(node: Node, ctx: Ctx) -> bool:
    """True when this `if` is the `else if` continuation of its parent.

    Three grammars spell it the same way: an `else_clause` holding a single `if`.
    A chain of them is one decision, not four levels of nesting.
    """
    if node.type not in ("if_statement", "if_expression"):
        return False
    parent = node.parent
    if parent is None or parent.type != "else_clause":
        return False
    named = [c for c in parent.named_children if not _is_comment(c)]
    return len(named) == 1 and named[0].id == node.id


# --------------------------------------------------------------------------- #
# Rule: junk_identifier
# --------------------------------------------------------------------------- #

def _binding_names(node: Node, ctx: Ctx) -> Iterator[Node]:
    """Yield identifier nodes that introduce a new local binding."""
    fam = ctx.family
    if fam == "rust":
        if node.type in ("let_declaration", "parameter", "closure_parameter"):
            pattern = node.child_by_field_name("pattern")
            if pattern is not None and pattern.type == "identifier":
                yield pattern
        return
    if fam == "python":
        if node.type == "parameters":
            for child in node.named_children:
                if child.type == "identifier":
                    yield child
        elif node.type in ("default_parameter", "typed_default_parameter", "typed_parameter"):
            name = node.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                yield name
        elif node.type == "assignment":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                yield left
        return
    if node.type == "variable_declarator":
        name = node.child_by_field_name("name")
        if name is not None and name.type == "identifier":
            yield name
    elif node.type == "formal_parameters":
        for child in node.named_children:
            if child.type == "identifier":
                yield child
    elif node.type in ("required_parameter", "optional_parameter"):
        pat = node.child_by_field_name("pattern")
        if pat is not None and pat.type == "identifier":
            yield pat


_TEMP_API = re.compile(
    r"tempfile|tempdir|mkdtemp|mkstemp|mktemp|NamedTemporaryFile|TemporaryDirectory|TempDir|temp_dir",
)


def rule_junk_identifier(ctx: Ctx) -> Iterator[Finding]:
    """Placeholder / graveyard binding names.

    Deliberately silent on 1-2 character names. Short is not the same as bad,
    and a length heuristic is exactly the kind of inverted signal that flags
    `canReusePreviousArticle` while letting `const xx = d[47]` through.
    """
    seen: set[tuple[int, int]] = set()
    for node in ctx.nodes:
        for name in _binding_names(node, ctx):
            text = ctx.text(name)
            if not (_JUNK_PATTERN.search(text) or text.lower() in _JUNK_EXACT):
                continue
            # A variable named for a temporary file that *is* a temporary file is
            # accurately named, not sloppy.
            if text.lower().startswith(("tmp", "temp")) or "_temp" in text.lower():
                if name.parent is not None and _TEMP_API.search(ctx.text(name.parent)):
                    continue
            key = (name.start_byte, name.end_byte)
            if key in seen:
                continue
            seen.add(key)
            yield _find(
                ctx, "junk_identifier", name,
                f'placeholder name `{text}` — name it after what it holds, or delete it',
            )


# --------------------------------------------------------------------------- #
# Rule: commented_out_code
# --------------------------------------------------------------------------- #

_COMMENT_MARK = re.compile(r"^\s*(?://+|/\*+|\*+|#+|--+)\s?")
_CODE_HINT = re.compile(
    r"=\s*\S|=>|\breturn\b|\bconst\b|\blet\b|\bvar\b|\bfunction\b|\bdef\b|\bclass\b"
    r"|\bif\s*\(|\bfor\s*\(|\bimport\b|\bfrom\b|\bprint\s*\(|\bawait\b|\bself\.",
)
_PROSE_TAIL = re.compile(r"[.!?]\s*$")
_DOC_COMMENT = re.compile(r"\s*(?:///|//!|/\*\*|/\*!)")

_COMMENT_STMT_TYPES = {
    "js": {
        "expression_statement", "lexical_declaration", "variable_declaration",
        "return_statement", "if_statement", "for_statement",
    },
    "python": {
        "expression_statement", "assignment", "return_statement", "if_statement",
        "for_statement", "import_statement", "call",
    },
    "rust": {
        "expression_statement", "let_declaration", "return_expression",
        "if_expression", "for_expression", "use_declaration", "macro_invocation",
    },
}


def _is_standalone_comment(node: Node, ctx: Ctx) -> bool:
    """True if the comment is alone on its line (not trailing live code)."""
    prefix = ctx.source[
        ctx.source.rfind(b"\n", 0, node.start_byte) + 1 : node.start_byte
    ].decode("utf-8", "replace")
    return not prefix.strip()


def rule_commented_out_code(ctx: Ctx) -> Iterator[Finding]:
    """A comment whose body parses as a statement in the same language."""
    fam = ctx.family
    for node in ctx.nodes:
        if not _is_comment(node):
            continue
        if not _is_standalone_comment(node, ctx):
            continue  # a trailing suppression on live code is not dead code
        raw = ctx.text(node)
        if _DOC_COMMENT.match(raw):
            continue  # doc examples and `/// <reference>` directives are not dead code
        body = _COMMENT_MARK.sub("", raw).strip().rstrip("*/").strip()
        if len(body) < 8 or _PROSE_TAIL.search(body) or not _CODE_HINT.search(body):
            continue
        try:
            sub = parse(ctx.lang, body.encode("utf-8")).root_node
        except Exception:
            continue
        if sub.has_error:
            continue
        named = [c for c in sub.named_children if not _is_comment(c)]
        if len(named) != 1:
            continue
        stmt = named[0]
        if stmt.type in ("identifier", "string", "expression_statement") and stmt.type == "identifier":
            continue
        if stmt.type == "expression_statement":
            inner = [c for c in stmt.named_children]
            if not inner or inner[0].type in ("identifier", "string", "binary_expression"):
                continue
        if stmt.type not in _COMMENT_STMT_TYPES[fam]:
            continue
        yield _find(
            ctx, "commented_out_code", node,
            "commented-out code — delete it; git remembers",
        )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    fn: Callable[[Ctx], Iterator[Finding]]
    default: bool
    summary: str


RULES: tuple[Rule, ...] = (
    Rule("config_fallback", rule_config_fallback, True,
         "env var silently defaults to a dev/local/secret value"),
    Rule("swallowed_error", rule_swallowed_error, True,
         "handler body discards the error"),
    Rule("error_to_empty_success", rule_error_to_empty_success, True,
         "handler returns an empty value, hiding failure from the caller"),
    Rule("elision_comment", rule_elision_comment, True,
         "truncation-artifact comment (\"rest of implementation\")"),
    Rule("deep_nesting", rule_deep_nesting, True,
         "control nesting depth >= 5 inside one function"),
    Rule("narrator_comment", rule_narrator_comment, True,
         "comment restates the statement it precedes"),
    Rule("junk_identifier", rule_junk_identifier, True,
         "placeholder/graveyard name (tmp, data1, foo, _old, _backup)"),
    Rule("commented_out_code", rule_commented_out_code, False,
         "comment that parses as code"),
    Rule("suppression_without_reason", rule_suppression_without_reason, False,
         "lint/type suppression with no stated reason"),
)

RULE_BY_ID = {r.id: r for r in RULES}
