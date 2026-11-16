"""File selection and diff scoping at the CLI boundary."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nits.cli import _iter_files, main


def test_generated_files_are_not_scored(tmp_path: Path):
    """`routeTree.gen.ts` is written by a tool; its suppressions are not the author's."""
    (tmp_path / "routeTree.gen.ts").write_text("/* eslint-disable */\nexport const a = 1\n")
    (tmp_path / "schema.generated.ts").write_text("export const b = 1\n")
    (tmp_path / "app.ts").write_text("export const c = 1\n")
    assert [p.name for p in _iter_files(tmp_path)] == ["app.ts"]
