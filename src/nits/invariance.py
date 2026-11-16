"""Meaning-preserving transforms, used to prove a check measures structure.

A hygiene signal that moves when you re-indent the same code is measuring
whitespace. Every strict transform below must leave the finding multiset
identical; `check_invariance` is the test that enforces it, and it runs in CI.

Transforms that are *not* expected to be invariant (comment paraphrasing) are
reported separately as sensitivity, rather than quietly excluded.
"""

from __future__ import annotations

import re

from tree_sitter import Node

from .lang import parse, walk


def _indent_unit(source: str) -> int:
    """Infer the file's indentation step instead of assuming four spaces.

    Assuming 4 would collapse a 1-space-indented file to column zero and silently
    invalidate it, which is the kind of transform bug that makes an invariance
    suite lie.
    """
    from math import gcd

    unit = 0
    for line in source.splitlines():
        if not line.strip():
            continue
        lead = len(line) - len(line.lstrip(" \t"))
        if lead:
            unit = gcd(unit, lead)
    return max(unit, 1)


def reindent(source: str, lang: str, width: int = 2) -> str:
    """Rescale leading indentation, preserving relative depth.

    Strict-invariant: nesting is read from the AST, so this must not move a
    single finding.
    """
    unit = _indent_unit(source)
    out = []
    for line in source.splitlines(keepends=True):
        stripped = line.lstrip(" \t")
        lead = line[: len(line) - len(stripped)]
        if not lead or not stripped.strip():
            out.append(line)
            continue
        spaces = lead.replace("\t", " " * unit)
        level, rem = divmod(len(spaces), unit)
        out.append(" " * (level * width + rem) + stripped)
    return "".join(out)


def use_tabs(source: str, lang: str) -> str:
    unit = _indent_unit(source)
    out = []
    for line in source.splitlines(keepends=True):
        stripped = line.lstrip(" \t")
        lead = line[: len(line) - len(stripped)]
        if not lead or not stripped.strip():
            out.append(line)
            continue
        spaces = lead.replace("\t", " " * unit)
        level, rem = divmod(len(spaces), unit)
        out.append("\t" * level + " " * rem + stripped)
    return "".join(out)


def trailing_whitespace(source: str, lang: str) -> str:
    return "\n".join(line + "  " if line.strip() else line for line in source.splitlines()) + "\n"


def blank_line_padding(source: str, lang: str) -> str:
    """Insert blank lines, shifting every line number."""
    return "\n".join("\n" + line if line.strip() and not line.startswith((" ", "\t")) else line
                     for line in source.splitlines()) + "\n"


def quote_style(source: str, lang: str) -> str:
    """Swap quote characters on simple string literals."""
    tree = parse(lang, source.encode())
    edits: list[tuple[int, int, str]] = []
    for node in walk(tree.root_node):
        if node.type != "string":
            continue
        raw = source[node.start_byte : node.end_byte]
        if len(raw) < 2 or raw[:3] in ('"""', "'''"):
            continue
        inner = raw[1:-1]
        if raw.startswith('"') and raw.endswith('"') and "'" not in inner and "\\" not in inner:
            edits.append((node.start_byte, node.end_byte, "'" + inner + "'"))
        elif raw.startswith("'") and raw.endswith("'") and '"' not in inner and "\\" not in inner:
            edits.append((node.start_byte, node.end_byte, '"' + inner + '"'))
    buf = source
    for start, end, repl in sorted(edits, reverse=True):
        buf = buf[:start] + repl + buf[end:]
    return buf


def rename_catch_binding(source: str, lang: str, new_name: str = "caughtFailureDetail") -> str:
    """Rename the exception binding in every handler.

    Strict-invariant: our analysers look at handler *shape*, so a rename must
    not hide a swallowed error.
    """
    tree = parse(lang, source.encode())
    edits: list[tuple[int, int, str]] = []

    def body_of(handler: Node) -> Node | None:
        for child in handler.named_children:
            if child.type in ("statement_block", "block"):
                return child
        return None

    for node in walk(tree.root_node):
        if node.type not in ("catch_clause", "except_clause"):
            continue
        binding: Node | None = None
        if node.type == "except_clause":
            # python: the binding is the identifier following an `as`
            kids = node.children
            for i, kid in enumerate(kids):
                if kid.type == "as" and i + 1 < len(kids) and kids[i + 1].type == "identifier":
                    binding = kids[i + 1]
                    break
        else:
            for child in node.named_children:
                if child.type == "identifier":
                    binding = child
                    break
        if binding is None:
            continue
        old = source[binding.start_byte : binding.end_byte]
        body = body_of(node)
        if body is None:
            continue
        for cand in walk(body):
            if cand.type == "identifier" and source[cand.start_byte : cand.end_byte] == old:
                edits.append((cand.start_byte, cand.end_byte, new_name))
    buf = source
    for start, end, repl in sorted(edits, reverse=True):
        buf = buf[:start] + repl + buf[end:]
    return buf


_SYNONYMS = {
    "total": "aggregate", "loop": "iterate", "items": "elements", "item": "element",
    "check": "verify", "return": "hand back", "value": "quantity", "user": "account",
    "error": "fault", "update": "refresh", "create": "build", "delete": "remove",
    "get": "fetch", "set": "store", "add": "append", "parse": "decode",
}


def paraphrase_comments(source: str, lang: str) -> str:
    """Rewrite comment words with synonyms. Sensitivity probe, not invariance."""
    out = []
    for line in source.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(("//", "#", "*", "/*")):
            def swap(m: re.Match) -> str:
                return _SYNONYMS.get(m.group(0).lower(), m.group(0))
            out.append(re.sub(r"[A-Za-z]+", swap, line))
        else:
            out.append(line)
    return "".join(out)


#: Transforms where a changed finding set is a bug.
STRICT = {
    "reindent:2": lambda s, l: reindent(s, l, 2),
    "reindent:8": lambda s, l: reindent(s, l, 8),
    "tabs": use_tabs,
    "trailing_whitespace": trailing_whitespace,
    "blank_line_padding": blank_line_padding,
    "quote_style": quote_style,
    "rename_catch_binding": rename_catch_binding,
}

#: Transforms whose effect on text-based rules is a known, reported limit.
SENSITIVITY = {
    "paraphrase_comments": paraphrase_comments,
}
