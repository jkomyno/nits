"""Language registry.

One tree-sitter grammar per language family. TypeScript's grammar parses plain
JavaScript, so `.js`/`.mjs`/`.cjs` reuse it; JSX-bearing files use the TSX
grammar. Adding a language means adding an entry here plus adapter branches in
`rules`, never a new rule set.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import tree_sitter_python as _py
import tree_sitter_rust as _rs
import tree_sitter_typescript as _ts
from tree_sitter import Language, Node, Parser, Tree

EXT_TO_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "ts",
    ".mts": "ts",
    ".cts": "ts",
    ".tsx": "tsx",
    ".js": "ts",
    ".mjs": "ts",
    ".cjs": "ts",
    ".jsx": "tsx",
    ".rs": "rust",
}

#: Language families that share a rule implementation.
FAMILY = {"python": "python", "ts": "js", "tsx": "js", "rust": "rust"}


def lang_for_path(path: str | Path) -> str | None:
    return EXT_TO_LANG.get(Path(path).suffix.lower())


@lru_cache(maxsize=None)
def _language(lang: str) -> Language:
    if lang == "python":
        return Language(_py.language())
    if lang == "ts":
        return Language(_ts.language_typescript())
    if lang == "tsx":
        return Language(_ts.language_tsx())
    if lang == "rust":
        return Language(_rs.language())
    raise ValueError(f"unsupported language: {lang}")


def parse(lang: str, source: bytes) -> Tree:
    """Parse `source`. A fresh Parser per call: tree-sitter parsers are stateful."""
    return Parser(_language(lang)).parse(source)


def walk(node: Node) -> "list[Node]":
    """Depth-first pre-order traversal, iterative to stay flat on deep files."""
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(reversed(cur.children))
