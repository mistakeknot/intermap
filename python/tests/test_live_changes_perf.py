"""Performance and mode behavior tests for live_changes."""

import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

import intermap.live_changes as live_changes_mod
from intermap.live_changes import get_live_changes


def _run(cmd, cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _init_repo(path: Path) -> None:
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "test@test.com"], path)
    _run(["git", "config", "user.name", "Test"], path)


def _module_source(file_idx: int, version: int) -> str:
    lines = [f"# module {file_idx} v{version}", "", f"class C{file_idx}:"]
    lines.append("    def __init__(self):")
    lines.append(f"        self.base = {file_idx + version}")
    lines.append("")
    for fn in range(35):
        lines.append(f"def func_{file_idx}_{fn}():")
        lines.append(f"    value = {file_idx + fn + version}")
        lines.append("    return value")
        lines.append("")
    lines.append(f"def class_method_{file_idx}(obj):")
    lines.append("    return obj.base")
    return "\n".join(lines) + "\n"


def _set_mode(mode: str):
    prev = os.environ.get("INTERMAP_LIVE_CHANGES_MODE")
    os.environ["INTERMAP_LIVE_CHANGES_MODE"] = mode
    return prev


def _restore_mode(prev: str | None):
    if prev is None:
        os.environ.pop("INTERMAP_LIVE_CHANGES_MODE", None)
    else:
        os.environ["INTERMAP_LIVE_CHANGES_MODE"] = prev


def _clear_live_changes_caches() -> None:
    live_changes_mod._PY_SYMBOL_CACHE.clear()
    live_changes_mod._PY_SYMBOL_CACHE_BYTES = 0
    live_changes_mod._BASELINE_SYMBOL_CACHE.clear()
    live_changes_mod._BASELINE_SYMBOL_CACHE_BYTES = 0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    # Inclusive percentile keeps stable behavior for short samples.
    return statistics.quantiles(values, n=100, method="inclusive")[94]


def _benchmark_mode(repo: Path, mode: str, runs: int = 35, warmup: int = 5) -> float:
    prev = _set_mode(mode)
    try:
        timings = []
        for _ in range(runs):
            start = time.perf_counter()
            result = get_live_changes(str(repo), baseline="HEAD~1")
            timings.append(time.perf_counter() - start)
            assert result["total_files"] == 10
        stable = timings[warmup:]
        return statistics.median(stable)
    finally:
        _restore_mode(prev)


def _benchmark_mode_stats(
    repo: Path, mode: str, runs: int = 35, warmup: int = 5,
) -> dict[str, float]:
    prev = _set_mode(mode)
    try:
        timings = []
        for _ in range(runs):
            start = time.perf_counter()
            result = get_live_changes(str(repo), baseline="HEAD~1")
            timings.append(time.perf_counter() - start)
            assert result["total_files"] == 10
        stable = timings[warmup:]
        return {
            "median": statistics.median(stable),
            "p95": _p95(stable),
            "cold": timings[0],
        }
    finally:
        _restore_mode(prev)


def _measure_cold_call_median(repo: Path, mode: str, runs: int = 7) -> float:
    prev = _set_mode(mode)
    try:
        samples = []
        for _ in range(runs):
            _clear_live_changes_caches()
            start = time.perf_counter()
            result = get_live_changes(str(repo), baseline="HEAD~1")
            samples.append(time.perf_counter() - start)
            assert result["total_files"] == 10
        return statistics.median(samples)
    finally:
        _restore_mode(prev)


def _prepare_repo_with_changed_and_unchanged_files(repo: Path) -> str:
    # Initial commit with 15 Python files.
    for i in range(15):
        (repo / f"mod_{i}.py").write_text(_module_source(i, version=0))
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "base"], repo)

    # Commit touching 10 files; remaining 5 stay unchanged.
    for i in range(10):
        (repo / f"mod_{i}.py").write_text(_module_source(i, version=1))
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "changed"], repo)

    return _run(["git", "rev-parse", "HEAD"], repo)


def test_live_changes_mode_parity_for_file_status(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _prepare_repo_with_changed_and_unchanged_files(repo)

    prev = _set_mode("legacy")
    try:
        legacy = get_live_changes(str(repo), baseline="HEAD~1")
    finally:
        _restore_mode(prev)

    prev = _set_mode("optimized")
    try:
        optimized = get_live_changes(str(repo), baseline="HEAD~1")
    finally:
        _restore_mode(prev)

    legacy_pairs = sorted((c["file"], c["status"]) for c in legacy["changes"])
    optimized_pairs = sorted((c["file"], c["status"]) for c in optimized["changes"])
    assert legacy_pairs == optimized_pairs
    assert len(legacy_pairs) == 10


def test_live_changes_optimized_mode_reflects_rapid_successive_edits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    f = repo / "rapid.py"
    f.write_text("def foo():\n    return 1\n")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "base"], repo)

    # First change (same file size as second change; symbol name changes).
    f.write_text("def bar():\n    return 2\n")
    prev = _set_mode("optimized")
    try:
        first = get_live_changes(str(repo), baseline="HEAD")
        assert first["total_files"] == 1
        first_symbols = [s["name"] for s in first["changes"][0]["symbols_affected"]]
        assert "bar" in first_symbols
    finally:
        _restore_mode(prev)

    # Second immediate change (within typical cache TTL windows).
    f.write_text("def baz():\n    return 3\n")
    prev = _set_mode("optimized")
    try:
        second = get_live_changes(str(repo), baseline="HEAD")
        assert second["total_files"] == 1
        assert second["changes"][0]["hunks"], "expected fresh diff hunks after second edit"
        second_symbols = [s["name"] for s in second["changes"][0]["symbols_affected"]]
        assert "baz" in second_symbols
        assert "bar" not in second_symbols
    finally:
        _restore_mode(prev)


def test_live_changes_invalid_mode_falls_back_to_legacy(tmp_path, caplog):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    f = repo / "mode.py"
    f.write_text("def foo():\n    return 1\n")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "base"], repo)
    f.write_text("def foo():\n    return 2\n")

    prev = _set_mode("legacy")
    try:
        legacy = get_live_changes(str(repo), baseline="HEAD")
    finally:
        _restore_mode(prev)

    prev = _set_mode("bogus")
    try:
        with caplog.at_level("WARNING", logger="intermap.live_changes"):
            result = get_live_changes(str(repo), baseline="HEAD")
        assert result["total_files"] == 1
        assert any(r.message == "live_changes.invalid_mode" for r in caplog.records)
    finally:
        _restore_mode(prev)

    def _summary(payload):
        return sorted(
            (
                c["file"],
                c["status"],
                sorted(s["name"] for s in c["symbols_affected"]),
            )
            for c in payload["changes"]
        )

    assert _summary(result) == _summary(legacy)


def test_live_changes_optimized_mode_improves_repeated_call_median_by_15_percent(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    head_sha = _prepare_repo_with_changed_and_unchanged_files(repo)

    legacy_median = _benchmark_mode(repo, "legacy")
    optimized_median = _benchmark_mode(repo, "optimized")

    if legacy_median <= 0:
        raise AssertionError("invalid legacy median timing")

    improvement = (legacy_median - optimized_median) / legacy_median
    env_meta = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "commit_sha": head_sha,
        "baseline": "HEAD~1",
        "legacy_mode": "legacy",
        "optimized_mode": "optimized",
    }

    assert improvement >= 0.15, (
        "expected >=15% median latency improvement for repeated identical calls; "
        f"legacy={legacy_median:.6f}s optimized={optimized_median:.6f}s "
        f"improvement={improvement:.2%} env={env_meta}"
    )


def test_live_changes_optimized_mode_p95_not_worse_than_legacy_by_20_percent(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _prepare_repo_with_changed_and_unchanged_files(repo)

    legacy = _benchmark_mode_stats(repo, "legacy")
    optimized = _benchmark_mode_stats(repo, "optimized")

    assert optimized["p95"] <= legacy["p95"] * 1.20, (
        "optimized p95 should not regress materially versus legacy; "
        f"legacy_p95={legacy['p95']:.6f}s optimized_p95={optimized['p95']:.6f}s"
    )


def test_live_changes_optimized_mode_cold_call_not_more_than_75_percent_slower(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _prepare_repo_with_changed_and_unchanged_files(repo)

    legacy_cold = _measure_cold_call_median(repo, "legacy")
    optimized_cold = _measure_cold_call_median(repo, "optimized")

    assert optimized_cold <= legacy_cold * 1.75, (
        "optimized cold-call median regressed beyond tolerance; "
        f"legacy={legacy_cold:.6f}s optimized={optimized_cold:.6f}s"
    )


def test_live_changes_optimized_mode_reuses_baseline_symbol_cache(
    tmp_path, monkeypatch,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    for i in range(4):
        (repo / f"del_{i}.py").write_text(
            "def alpha():\n"
            "    first = 1\n"
            "    second = 2\n"
            "    return first + second\n"
        )
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "base"], repo)

    for i in range(4):
        (repo / f"del_{i}.py").write_text(
            "def alpha():\n"
            "    first = 1\n"
            "    return first + second\n"
        )

    real_run = live_changes_mod.subprocess.run
    show_calls = 0

    def _counting_run(cmd, *args, **kwargs):
        nonlocal show_calls
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "show":
            show_calls += 1
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(live_changes_mod.subprocess, "run", _counting_run)
    _clear_live_changes_caches()

    prev = _set_mode("optimized")
    try:
        first = get_live_changes(str(repo), baseline="HEAD")
        first_show_calls = show_calls
        second = get_live_changes(str(repo), baseline="HEAD")
        second_show_calls = show_calls
    finally:
        _restore_mode(prev)

    assert first["total_files"] == 4
    assert second["total_files"] == 4
    assert first_show_calls >= 4
    assert second_show_calls == first_show_calls


def test_live_changes_symbol_cache_respects_byte_cap(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _prepare_repo_with_changed_and_unchanged_files(repo)

    monkeypatch.setattr(live_changes_mod, "_MAX_PY_SYMBOL_CACHE_BYTES", 1024)
    monkeypatch.setattr(live_changes_mod, "_MAX_PY_SYMBOL_CACHE_ENTRIES", 4096)
    _clear_live_changes_caches()

    prev = _set_mode("optimized")
    try:
        result = get_live_changes(str(repo), baseline="HEAD~1")
    finally:
        _restore_mode(prev)

    assert result["total_files"] == 10
    assert live_changes_mod._PY_SYMBOL_CACHE_BYTES <= 1024


def test_live_changes_skips_baseline_resolution_when_not_needed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    f = repo / "simple.py"
    f.write_text("def alpha():\n    return 1\n")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "base"], repo)

    # Non-deletion edit should not trigger baseline identity resolution.
    f.write_text("def alpha():\n    return 2\n")

    real_run = live_changes_mod.subprocess.run
    rev_parse_calls = 0

    def _counting_run(cmd, *args, **kwargs):
        nonlocal rev_parse_calls
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "rev-parse":
            rev_parse_calls += 1
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(live_changes_mod.subprocess, "run", _counting_run)
    _clear_live_changes_caches()

    prev = _set_mode("optimized")
    try:
        result = get_live_changes(str(repo), baseline="HEAD")
    finally:
        _restore_mode(prev)
    assert result["total_files"] == 1
    assert rev_parse_calls == 0

    # Legacy mode should also avoid baseline identity resolution.
    f.write_text("def alpha():\n    return 3\n")
    prev = _set_mode("legacy")
    try:
        result = get_live_changes(str(repo), baseline="HEAD")
    finally:
        _restore_mode(prev)
    assert result["total_files"] == 1
    assert rev_parse_calls == 0
