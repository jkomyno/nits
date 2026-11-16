"""Diff scoping.

Legacy debt is the reason full-repo gates get switched off. `nits` never scores
code you did not touch: `parse_diff` turns `git diff -U0` hunks into added-line
ranges, and findings outside those ranges are dropped.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True, slots=True)
class ChangedFile:
    path: str
    added: tuple[tuple[int, int], ...]  # 1-indexed inclusive ranges
    is_new: bool = False

    def contains(self, start: int, end: int) -> bool:
        return any(start <= hi and end >= lo for lo, hi in self.added)


def _clean_target(line: str) -> str | None:
    """Extract a path from a `+++ ` header line."""
    target = line[4:].strip()
    if target.startswith('"') and target.endswith('"'):
        target = target[1:-1]
    if target.startswith("b/"):
        target = target[2:]
    if target == "/dev/null":
        return None
    return target or None


def parse_diff(diff_text: str) -> dict[str, ChangedFile]:
    files: dict[str, tuple[bool, list[tuple[int, int]]]] = {}
    path: str | None = None
    is_new = False
    ranges: list[tuple[int, int]] = []

    def flush() -> None:
        nonlocal path, is_new, ranges
        if path:
            files[path] = (is_new, ranges)
        path, is_new, ranges = None, False, []

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush()
            continue
        if line.startswith("new file mode"):
            is_new = True
            continue
        if line.startswith("+++ "):
            path = _clean_target(line)
            continue
        m = _HUNK.match(line)
        if m and path:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count > 0:
                ranges.append((start, start + count - 1))
    flush()

    out: dict[str, ChangedFile] = {}
    for name, (new, spans) in files.items():
        if new:
            # a new file is entirely added
            out[name] = ChangedFile(name, ((1, 10**9),), True)
        elif spans:
            out[name] = ChangedFile(name, tuple(spans), False)
    return out


def git_diff(args: list[str] | None = None, cwd: str | None = None) -> str:
    cmd = ["git", "diff", "--no-color", "-U0", "--no-ext-diff", *(args or ["HEAD"])]
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode not in (0, 1):
        raise RuntimeError(f"git diff failed: {res.stderr.strip()}")
    return res.stdout
