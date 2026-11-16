"""Diff parsing. Attribution is the whole product: flag only what changed."""

from __future__ import annotations

from nits.diff import parse_diff

SAMPLE = """\
diff --git a/src/a.ts b/src/a.ts
index 111..222 100644
--- a/src/a.ts
+++ b/src/a.ts
@@ -3,0 +4,3 @@
+const a = 1;
+const b = 2;
+const c = 3;
@@ -10,2 +13,1 @@
-old
+new
diff --git a/src/new.py b/src/new.py
new file mode 100644
index 000..333
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+x = 1
+y = 2
diff --git a/gone.rs b/gone.rs
deleted file mode 100644
--- a/gone.rs
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
"""


def test_added_ranges():
    files = parse_diff(SAMPLE)
    assert files["src/a.ts"].added == ((4, 6), (13, 13))
    assert files["src/a.ts"].is_new is False


def test_new_file_is_entirely_added():
    files = parse_diff(SAMPLE)
    assert files["src/new.py"].is_new is True
    assert files["src/new.py"].contains(1, 1)
    assert files["src/new.py"].contains(9999, 9999)


def test_deleted_files_are_ignored():
    assert "gone.rs" not in parse_diff(SAMPLE)


def test_contains_is_span_aware():
    changed = parse_diff(SAMPLE)["src/a.ts"]
    assert changed.contains(5, 5)
    assert not changed.contains(7, 9)
    # a finding spanning a hunk boundary still counts
    assert changed.contains(3, 4)


def test_empty_diff():
    assert parse_diff("") == {}
