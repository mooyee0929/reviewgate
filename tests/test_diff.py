from __future__ import annotations

from pathlib import Path

from reviewgate.diff import glob_match, is_ignored, parse_diff

FIXTURES = Path(__file__).parent / "fixtures" / "diffs"


def test_parse_mixed_diff_statuses_and_lines() -> None:
    files = {f.path: f for f in parse_diff((FIXTURES / "mixed.diff").read_text())}
    assert set(files) == {
        "app/main.py",
        "new_name.txt",
        "gone.py",
        "logo.png",
        "package-lock.json",
        "src/engine.cpp",
    }
    main = files["app/main.py"]
    assert main.status == "modified"
    assert main.changed_line_numbers == frozenset({11, 12, 13, 43, 44})
    assert [line.text for line in main.hunks[0].added] == [
        "    import pdb; pdb.set_trace()",
        "    result = transform(data)",
        "    return result",
    ]
    assert main.added_line_count == 5
    renamed = files["new_name.txt"]
    assert renamed.status == "renamed"
    assert renamed.old_path == "old_name.txt"
    assert renamed.source_path == "old_name.txt"
    assert main.source_path == "app/main.py"
    assert renamed.changed_line_numbers == frozenset({2})
    assert files["logo.png"].is_binary and files["logo.png"].status == "added"
    assert files["src/engine.cpp"].changed_line_numbers == frozenset({6})


def test_hunk_ranges_and_removed_lines_use_old_numbers() -> None:
    (f,) = [x for x in parse_diff((FIXTURES / "mixed.diff").read_text()) if x.path == "app/main.py"]
    first, second = f.hunks
    assert (first.old_start, first.old_count, first.old_end) == (10, 6, 15)
    assert (first.new_start, first.new_count, first.new_end) == (10, 8, 17)
    assert [(r.number, r.text) for r in first.removed] == [(11, "    return data")]
    assert [(a.number, a.text) for a in first.added][:1] == [
        (11, "    import pdb; pdb.set_trace()")
    ]
    assert (second.old_start, second.old_end) == (40, 42)
    assert second.removed == ()
    assert [r.text for r in f.removed_lines] == ["    return data"]


def test_removed_numbers_after_context() -> None:
    text = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        "@@ -10,3 +10,2 @@\n ctx\n-old\n+new\n ctx2\n-gone\n"
    )
    (f,) = parse_diff(text)
    assert [(r.number, r.text) for r in f.hunks[0].removed] == [(11, "old"), (13, "gone")]
    assert [(a.number, a.text) for a in f.hunks[0].added] == [(11, "new")]


def test_pure_insert_hunk() -> None:
    text = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -5,0 +6,2 @@\n+a\n+b\n"
    (f,) = parse_diff(text)
    hunk = f.hunks[0]
    assert (hunk.old_start, hunk.old_count, hunk.old_end) == (5, 0, 4)
    assert (hunk.new_start, hunk.new_count, hunk.new_end) == (6, 2, 7)
    assert hunk.removed == ()


def test_deleted_file_keeps_removed_lines() -> None:
    files = {f.path: f for f in parse_diff((FIXTURES / "mixed.diff").read_text())}
    gone = files["gone.py"]
    assert gone.status == "deleted"
    assert len(gone.hunks) == 1
    assert [(r.number, r.text) for r in gone.removed_lines] == [(1, "def gone():"), (2, "    pass")]
    assert gone.added_lines == []


def test_parse_added_file() -> None:
    (f,) = parse_diff((FIXTURES / "secret.diff").read_text())
    assert f.status == "added"
    assert f.changed_line_numbers == frozenset({1, 2, 3, 4})
    assert f.hunks[0].old_count == 0
    assert f.removed_lines == []


def test_single_line_hunk_header_defaults_to_count_one() -> None:
    text = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
    (f,) = parse_diff(text)
    assert (f.hunks[0].old_count, f.hunks[0].new_count) == (1, 1)


def test_glob_match_and_is_ignored() -> None:
    assert glob_match("a/b/c.lock", "**/*.lock")
    assert glob_match("c.lock", "**/*.lock")
    assert not glob_match("c.lockx", "**/*.lock")
    assert glob_match("vendor/x/y.py", "vendor/**")
    assert glob_match("vendor", "vendor/**")
    assert not glob_match("src/vendor/x.py", "vendor/**")
    assert glob_match("src/gen.py", "src/*.py")
    assert is_ignored("a/package-lock.json", ["**/package-lock.json", "*.png"])
    assert not is_ignored("a/b.py", ["**/package-lock.json", "*.png"])
