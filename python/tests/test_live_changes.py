"""Tests for live change awareness."""

import logging
import os
import subprocess

import pytest

from intermap.live_changes import get_live_changes


# Resolve intermap root relative to this test file
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
INTERMAP_ROOT = os.path.normpath(os.path.join(_TESTS_DIR, "../.."))


# --- Fixture-based tests (synthetic git repos) ---


def _init_git_repo(path):
    """Initialize a git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path), capture_output=True, check=True,
    )


def test_output_structure(tmp_path):
    """Output has required fields."""
    _init_git_repo(tmp_path)
    (tmp_path / "hello.py").write_text("def hello():\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    assert "project" in result
    assert "baseline" in result
    assert "changes" in result
    assert isinstance(result["changes"], list)
    assert "total_files" in result
    assert "total_symbols_affected" in result


def test_detects_modified_file(tmp_path):
    """Detects a file modified since baseline."""
    _init_git_repo(tmp_path)
    f = tmp_path / "main.py"
    f.write_text("def original():\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )
    f.write_text("def original():\n    return 42\n\ndef added():\n    pass\n")

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    assert result["total_files"] == 1
    assert result["changes"][0]["file"] == "main.py"
    assert result["changes"][0]["status"] == "modified"


def test_detects_added_file(tmp_path):
    """Detects a newly added file."""
    _init_git_repo(tmp_path)
    (tmp_path / "old.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )
    (tmp_path / "new.py").write_text("def new_func():\n    pass\n")
    subprocess.run(["git", "add", "new.py"], cwd=str(tmp_path), capture_output=True, check=True)

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    files = {c["file"] for c in result["changes"]}
    assert "new.py" in files


def test_symbol_annotation(tmp_path):
    """Changed function definition line should appear in symbols_affected."""
    _init_git_repo(tmp_path)
    f = tmp_path / "module.py"
    f.write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )
    # Modify the file — add a new function after bar
    f.write_text(
        "def foo():\n    return 1\n\ndef bar():\n    return 2\n\n"
        "def baz():\n    return 3\n"
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    change = result["changes"][0]
    symbol_names = [s["name"] for s in change["symbols_affected"]]
    # baz is new — its def line should be in the changed lines
    assert "baz" in symbol_names


def test_no_changes(tmp_path):
    """No changes returns empty list."""
    _init_git_repo(tmp_path)
    (tmp_path / "stable.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    assert result["total_files"] == 0
    assert result["changes"] == []


def test_change_fields(tmp_path):
    """Each change has required fields."""
    _init_git_repo(tmp_path)
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )
    f.write_text("x = 2\n")

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    for change in result["changes"]:
        assert "file" in change
        assert "status" in change
        assert "symbols_affected" in change


def test_symbol_annotation_body_edit_marks_enclosing_function(tmp_path):
    """Body-only edit should still map to the enclosing function symbol."""
    _init_git_repo(tmp_path)
    f = tmp_path / "body_edit.py"
    f.write_text(
        "def foo():\n"
        "    value = 1\n"
        "    return value\n\n"
        "def bar():\n"
        "    return 2\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    # Edit only inside foo() body, not the def line.
    f.write_text(
        "def foo():\n"
        "    value = 42\n"
        "    return value\n\n"
        "def bar():\n"
        "    return 2\n"
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    change = result["changes"][0]
    symbol_names = [s["name"] for s in change["symbols_affected"]]
    assert "foo" in symbol_names


def test_symbol_annotation_method_body_edit_marks_class_method(tmp_path):
    """Method body edit should map to the enclosing class method symbol."""
    _init_git_repo(tmp_path)
    f = tmp_path / "method_edit.py"
    f.write_text(
        "class Worker:\n"
        "    def run(self):\n"
        "        value = 1\n"
        "        return value\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)

    # Edit only method body, not class/def lines.
    f.write_text(
        "class Worker:\n"
        "    def run(self):\n"
        "        value = 9\n"
        "        return value\n"
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    change = result["changes"][0]
    symbol_names = [s["name"] for s in change["symbols_affected"]]
    assert "Worker.run" in symbol_names


def test_symbol_annotation_decorator_edit_marks_function(tmp_path):
    """Decorator-only edits should map to the decorated function symbol."""
    _init_git_repo(tmp_path)
    f = tmp_path / "decorator_edit.py"
    f.write_text(
        "@old_deco\n"
        "def foo():\n"
        "    return 1\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)

    f.write_text(
        "@new_deco\n"
        "def foo():\n"
        "    return 1\n"
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    change = result["changes"][0]
    symbol_names = [s["name"] for s in change["symbols_affected"]]
    assert "foo" in symbol_names


def test_symbol_annotation_decorator_edit_marks_method(tmp_path):
    """Decorator-only edits should map to the decorated class method symbol."""
    _init_git_repo(tmp_path)
    f = tmp_path / "decorator_method.py"
    f.write_text(
        "class Worker:\n"
        "    @old_deco\n"
        "    def run(self):\n"
        "        return 1\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)

    f.write_text(
        "class Worker:\n"
        "    @new_deco\n"
        "    def run(self):\n"
        "        return 1\n"
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    change = result["changes"][0]
    symbol_names = [s["name"] for s in change["symbols_affected"]]
    assert "Worker.run" in symbol_names


def test_symbol_annotation_pure_deletion_does_not_false_mark_symbols(tmp_path):
    """Pure deletion should not falsely mark unrelated symbols as affected."""
    _init_git_repo(tmp_path)
    f = tmp_path / "deletion_case.py"
    f.write_text(
        "def alpha():\n"
        "    return 1\n\n"
        "# trailing note\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)

    # Pure deletion outside symbol body.
    f.write_text(
        "def alpha():\n"
        "    return 1\n"
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    change = result["changes"][0]
    assert change["status"] == "modified"
    # Deletions should not create spurious symbol matches.
    assert change["symbols_affected"] == []


def test_symbol_annotation_pure_deletion_inside_symbol_marks_function(tmp_path):
    """Pure deletion inside a function body should mark the enclosing function."""
    _init_git_repo(tmp_path)
    f = tmp_path / "deletion_inside.py"
    f.write_text(
        "def alpha():\n"
        "    first = 1\n"
        "    second = 2\n"
        "    return first + second\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    # Pure deletion of one interior line from alpha().
    f.write_text(
        "def alpha():\n"
        "    first = 1\n"
        "    return first + second\n"
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    change = result["changes"][0]
    symbol_names = [s["name"] for s in change["symbols_affected"]]
    assert "alpha" in symbol_names


def test_legacy_mode_pure_deletion_outside_symbol_does_not_mark_symbols(tmp_path):
    """Legacy rollback mode preserves pre-hardening deletion attribution behavior."""
    _init_git_repo(tmp_path)
    f = tmp_path / "legacy_deletion.py"
    f.write_text(
        "def alpha():\n"
        "    return 1\n\n"
        "# trailing note\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    f.write_text(
        "def alpha():\n"
        "    return 1\n"
    )

    prev_mode = os.environ.get("INTERMAP_LIVE_CHANGES_MODE")
    os.environ["INTERMAP_LIVE_CHANGES_MODE"] = "legacy"
    try:
        result = get_live_changes(str(tmp_path), baseline="HEAD")
    finally:
        if prev_mode is None:
            os.environ.pop("INTERMAP_LIVE_CHANGES_MODE", None)
        else:
            os.environ["INTERMAP_LIVE_CHANGES_MODE"] = prev_mode

    assert result["changes"][0]["symbols_affected"] == []


def test_optimized_mode_pure_deletion_before_symbol_does_not_false_mark(tmp_path):
    """Deleting top-of-file comments should not mark the first symbol as changed."""
    _init_git_repo(tmp_path)
    f = tmp_path / "optimized_header_delete.py"
    f.write_text(
        "# heading\n\n"
        "def alpha():\n"
        "    return 1\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    f.write_text(
        "\n"
        "def alpha():\n"
        "    return 1\n"
    )

    prev_mode = os.environ.get("INTERMAP_LIVE_CHANGES_MODE")
    os.environ["INTERMAP_LIVE_CHANGES_MODE"] = "optimized"
    try:
        result = get_live_changes(str(tmp_path), baseline="HEAD")
    finally:
        if prev_mode is None:
            os.environ.pop("INTERMAP_LIVE_CHANGES_MODE", None)
        else:
            os.environ["INTERMAP_LIVE_CHANGES_MODE"] = prev_mode

    assert result["changes"][0]["symbols_affected"] == []


def test_optimized_mode_non_utf8_baseline_extraction_does_not_crash(tmp_path):
    """Old-side baseline extraction should be decode-safe for non-UTF8 files."""
    _init_git_repo(tmp_path)
    f = tmp_path / "latin1_case.py"
    f.write_bytes(
        b"# caf\xe9\n\n"
        b"def alpha():\n"
        b"    return 1\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    # Pure deletion above symbol to force old-side deletion attribution path.
    f.write_bytes(
        b"\n"
        b"def alpha():\n"
        b"    return 1\n"
    )

    prev_mode = os.environ.get("INTERMAP_LIVE_CHANGES_MODE")
    os.environ["INTERMAP_LIVE_CHANGES_MODE"] = "optimized"
    try:
        result = get_live_changes(str(tmp_path), baseline="HEAD")
    finally:
        if prev_mode is None:
            os.environ.pop("INTERMAP_LIVE_CHANGES_MODE", None)
        else:
            os.environ["INTERMAP_LIVE_CHANGES_MODE"] = prev_mode

    assert result["total_files"] == 1
    assert result["changes"][0]["status"] == "modified"
    assert result["changes"][0]["symbols_affected"] == []


def test_optimized_mode_rename_with_pure_deletion_preserves_symbol_attribution(tmp_path):
    """Rename + pure deletion should still resolve old-side symbols via old path."""
    _init_git_repo(tmp_path)
    old_path = tmp_path / "old_name.py"
    old_path.write_text(
        "def alpha():\n"
        "    first = 1\n"
        "    second = 2\n"
        "    return first + second\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    new_path = tmp_path / "new_name.py"
    subprocess.run(
        ["git", "mv", "old_name.py", "new_name.py"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )
    new_path.write_text(
        "def alpha():\n"
        "    first = 1\n"
        "    return first + second\n"
    )

    name_status = subprocess.run(
        ["git", "diff", "--name-status", "HEAD"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "\told_name.py\tnew_name.py" in name_status

    prev_mode = os.environ.get("INTERMAP_LIVE_CHANGES_MODE")
    os.environ["INTERMAP_LIVE_CHANGES_MODE"] = "optimized"
    try:
        result = get_live_changes(str(tmp_path), baseline="HEAD")
    finally:
        if prev_mode is None:
            os.environ.pop("INTERMAP_LIVE_CHANGES_MODE", None)
        else:
            os.environ["INTERMAP_LIVE_CHANGES_MODE"] = prev_mode

    by_file = {c["file"]: c for c in result["changes"]}
    assert by_file["new_name.py"]["status"] == "renamed"
    symbol_names = [s["name"] for s in by_file["new_name.py"]["symbols_affected"]]
    assert "alpha" in symbol_names


def test_optimized_mode_baseline_cache_tracks_moving_head(tmp_path):
    """Baseline cache must not return stale symbols when HEAD advances."""
    _init_git_repo(tmp_path)
    f = tmp_path / "moving_head.py"

    # Commit A
    f.write_text(
        "def alpha():\n"
        "    first = 1\n"
        "    second = 2\n"
        "    return first + second\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "A"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    # Warm baseline cache against HEAD=A with pure deletion.
    f.write_text(
        "def alpha():\n"
        "    first = 1\n"
        "    return first + second\n"
    )
    prev_mode = os.environ.get("INTERMAP_LIVE_CHANGES_MODE")
    os.environ["INTERMAP_LIVE_CHANGES_MODE"] = "optimized"
    try:
        first = get_live_changes(str(tmp_path), baseline="HEAD")
        first_symbols = [s["name"] for s in first["changes"][0]["symbols_affected"]]
        assert "alpha" in first_symbols
    finally:
        if prev_mode is None:
            os.environ.pop("INTERMAP_LIVE_CHANGES_MODE", None)
        else:
            os.environ["INTERMAP_LIVE_CHANGES_MODE"] = prev_mode

    # Commit B (HEAD moves and symbol name changes).
    f.write_text(
        "def beta():\n"
        "    first = 1\n"
        "    second = 2\n"
        "    return first + second\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "B"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    # New pure deletion relative to HEAD=B should report beta, not stale alpha.
    f.write_text(
        "def beta():\n"
        "    first = 1\n"
        "    return first + second\n"
    )

    prev_mode = os.environ.get("INTERMAP_LIVE_CHANGES_MODE")
    os.environ["INTERMAP_LIVE_CHANGES_MODE"] = "optimized"
    try:
        second = get_live_changes(str(tmp_path), baseline="HEAD")
    finally:
        if prev_mode is None:
            os.environ.pop("INTERMAP_LIVE_CHANGES_MODE", None)
        else:
            os.environ["INTERMAP_LIVE_CHANGES_MODE"] = prev_mode

    second_symbols = [s["name"] for s in second["changes"][0]["symbols_affected"]]
    assert "beta" in second_symbols
    assert "alpha" not in second_symbols


def test_optimized_mode_baseline_cache_tracks_moving_hex_like_ref(tmp_path):
    """Hex-like mutable refs (e.g., branch 'deadbeef') must not stale cache."""
    _init_git_repo(tmp_path)
    f = tmp_path / "moving_hex_ref.py"

    # Commit A + branch deadbeef at A.
    f.write_text(
        "def alpha():\n"
        "    first = 1\n"
        "    second = 2\n"
        "    return first + second\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "A"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "branch", "deadbeef"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    # Warm cache for baseline deadbeef at commit A.
    f.write_text(
        "def alpha():\n"
        "    first = 1\n"
        "    return first + second\n"
    )
    prev_mode = os.environ.get("INTERMAP_LIVE_CHANGES_MODE")
    os.environ["INTERMAP_LIVE_CHANGES_MODE"] = "optimized"
    try:
        first = get_live_changes(str(tmp_path), baseline="deadbeef")
    finally:
        if prev_mode is None:
            os.environ.pop("INTERMAP_LIVE_CHANGES_MODE", None)
        else:
            os.environ["INTERMAP_LIVE_CHANGES_MODE"] = prev_mode
    first_symbols = [s["name"] for s in first["changes"][0]["symbols_affected"]]
    assert "alpha" in first_symbols

    # Commit B, then move deadbeef -> B.
    f.write_text(
        "def beta():\n"
        "    first = 1\n"
        "    second = 2\n"
        "    return first + second\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "B"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "branch", "-f", "deadbeef", "HEAD"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    # Pure deletion against moved baseline deadbeef should report beta.
    f.write_text(
        "def beta():\n"
        "    first = 1\n"
        "    return first + second\n"
    )
    prev_mode = os.environ.get("INTERMAP_LIVE_CHANGES_MODE")
    os.environ["INTERMAP_LIVE_CHANGES_MODE"] = "optimized"
    try:
        second = get_live_changes(str(tmp_path), baseline="deadbeef")
    finally:
        if prev_mode is None:
            os.environ.pop("INTERMAP_LIVE_CHANGES_MODE", None)
        else:
            os.environ["INTERMAP_LIVE_CHANGES_MODE"] = prev_mode

    second_symbols = [s["name"] for s in second["changes"][0]["symbols_affected"]]
    assert "beta" in second_symbols
    assert "alpha" not in second_symbols


def test_symbol_annotation_non_python_file_has_no_symbol_annotations(tmp_path):
    """Non-Python files should not produce symbol annotations."""
    _init_git_repo(tmp_path)
    f = tmp_path / "notes.txt"
    f.write_text("line one\nline two\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    f.write_text("line one updated\nline two\n")

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    change = result["changes"][0]
    assert change["file"] == "notes.txt"
    assert change["symbols_affected"] == []


def test_extractor_error_emits_structured_debug_log(tmp_path, monkeypatch, caplog):
    """Extractor failure should emit the structured debug logging contract."""
    _init_git_repo(tmp_path)
    f = tmp_path / "log_case.txt"
    f.write_text("alpha\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )
    f.write_text("beta\n")

    class _FailingExtractor:
        def extract(self, _path):
            raise RuntimeError("boom")

    monkeypatch.setattr("intermap.live_changes.DefaultExtractor", lambda: _FailingExtractor())

    with caplog.at_level(logging.DEBUG, logger="intermap.live_changes"):
        result = get_live_changes(str(tmp_path), baseline="HEAD")

    assert result["total_files"] == 1
    change = result["changes"][0]
    assert change["symbols_affected"] == []

    matches = [r for r in caplog.records if r.message == "live_changes.extractor_error"]
    assert matches, "expected structured extractor error log event"
    record = matches[0]
    assert record.file == str(f)
    assert record.project_path == str(tmp_path)
    assert record.baseline == "HEAD"
    assert record.error_type == "RuntimeError"
    assert "boom" in record.error_message


def test_optimized_parser_mixed_modified_deleted_does_not_cross_file_hunks(tmp_path):
    """Deletion hunks must not be attributed to the previous modified file."""
    _init_git_repo(tmp_path)
    mod_file = tmp_path / "mod.py"
    del_file = tmp_path / "gone.py"
    mod_file.write_text("def alpha():\n    return 1\n")
    del_file.write_text("def beta():\n    return 2\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    mod_file.write_text("def alpha():\n    return 3\n")
    del_file.unlink()

    prev_mode = os.environ.get("INTERMAP_LIVE_CHANGES_MODE")
    os.environ["INTERMAP_LIVE_CHANGES_MODE"] = "optimized"
    try:
        result = get_live_changes(str(tmp_path), baseline="HEAD")
    finally:
        if prev_mode is None:
            os.environ.pop("INTERMAP_LIVE_CHANGES_MODE", None)
        else:
            os.environ["INTERMAP_LIVE_CHANGES_MODE"] = prev_mode

    by_file = {c["file"]: c for c in result["changes"]}
    assert by_file["mod.py"]["status"] == "modified"
    assert len(by_file["mod.py"]["hunks"]) == 1
    assert by_file["gone.py"]["status"] == "deleted"
    assert by_file["gone.py"]["hunks"], "expected deleted-file hunks in optimized mode"
