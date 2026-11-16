"""File selection and diff scoping at the CLI boundary."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nits.cli import _iter_files, main


def _repo(path: Path) -> None:
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(path)]
    subprocess.run([*git[:5], "init", "-q", str(path)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "init"], check=True)


SWALLOW = "def f():\n    try:\n        g()\n    except Exception:\n        return None\n"


def test_untracked_files_are_in_the_working_tree_diff(tmp_path: Path, capsys):
    """A new file nobody has `git add`-ed yet is still part of the change."""
    _repo(tmp_path)
    (tmp_path / "new.py").write_text(SWALLOW)
    main(["diff", "--cwd", str(tmp_path), "--json"])
    assert '"total": 1' in capsys.readouterr().out


def test_staged_diff_leaves_untracked_files_out(tmp_path: Path, capsys):
    _repo(tmp_path)
    (tmp_path / "new.py").write_text(SWALLOW)
    main(["diff", "--staged", "--cwd", str(tmp_path), "--json"])
    assert '"total": 0' in capsys.readouterr().out


def test_generated_files_are_not_scored(tmp_path: Path):
    """`routeTree.gen.ts` is written by a tool; its suppressions are not the author's."""
    (tmp_path / "routeTree.gen.ts").write_text("/* eslint-disable */\nexport const a = 1\n")
    (tmp_path / "schema.generated.ts").write_text("export const b = 1\n")
    (tmp_path / "app.ts").write_text("export const c = 1\n")
    assert [p.name for p in _iter_files(tmp_path)] == ["app.ts"]
