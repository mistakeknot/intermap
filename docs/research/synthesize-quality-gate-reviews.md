# Quality Gate Review Synthesis — Intermap Sprint

**Review Date:** 2026-02-23
**Scope:** 22 files changed (~1700 lines) across Go and Python
**Agents Launched:** 4 (fd-architecture, fd-correctness, fd-performance, fd-quality)
**Agents Completed:** 4 (100%)

---

## Executive Summary

The sprint adds three new MCP tools (cross-project dependency analysis, architecture pattern detection, live changes annotation) following established patterns. All agents completed successfully with **needs-changes** verdict across the board. Key blockers are performance degradation (HIGH: 2 findings) and correctness gaps in symbol detection (MEDIUM: convergence across 3 agents). The vendor extraction of `workspace.py` and `ignore.py` is sound but incomplete (stale copies remain). Estimated fix complexity: moderate to low; no architectural rework needed.

**Overall Verdict:** `needs-changes` — proceed with fixes, deploy after validation.

---

## Validation Report

| Metric | Result |
|--------|--------|
| Agents Validated | 4/4 (100%) |
| Structure Errors | 0 |
| Verdict Parse Errors | 0 |
| Malformed Output | 0 |

All agents produced valid, structured output with complete Findings Indexes and verified verdicts.

---

## Verdict Summary

| Agent | Status | Verdict | P0 | P1 | P2 | Key Finding |
|-------|--------|---------|-----|-----|-----|-------------|
| fd-architecture | CLEAN | needs-changes | 0 | 0 | 2M+2L | Stale vendor copies, symbol detection gap |
| fd-correctness | CLEAN | needs-changes | 0 | 0 | 4L+1I | Deletion hunk false positive, regex blind spots |
| fd-performance | ALERT | needs-changes | 0 | 2H | 2M+2L | No caching on MCP tool calls, git subprocess bottleneck |
| fd-quality | ALERT | needs-changes | 0 | 0 | 3M+6L+1I | Exception swallowing, body-change miss, regex patterns |

**Consolidated Verdict:** `needs-changes` — has blocking HIGH findings.

---

## Deduplicated Findings

### CRITICAL (Blocks Merge)

None. Highest severity is HIGH (performance).

### HIGH — BLOCKING

**P1. Full file read on every tool invocation — no caching (patterns.py:56-171)**
- **Severity:** HIGH
- **Agent:** fd-performance
- **Impact:** `detect_patterns` and `cross_project_deps` re-read and re-scan all source files from scratch on each MCP call. Go-level LRU cache only covers `project_registry`; Python subprocess-level `FileCache` dies on exit. On Demarch monorepo (33+ projects × hundreds of files), this is O(all_files) per invocation.
- **Files:** `python/intermap/patterns.py:56-171` (os.walk → Path.read_text for every .go/.py file)
- **Fix:** Add Go-level mtime-keyed cache for `detectPatterns` and `crossProjectDeps` handlers using existing cache infrastructure (`internal/cache/cache.go`).
- **Urgency:** Session-level latency impact; agents feel delays during active review.

**P2. Two sequential git subprocesses per call — no caching (live_changes.py:83-136)**
- **Severity:** HIGH
- **Agent:** fd-performance
- **Impact:** `get_live_changes` calls `git diff --name-status` then `git diff --unified=0` sequentially with 10-second timeout each. No Go-level cache entry for results. Repeated agent calls (progress checks) re-run both subprocesses. On large diffs (hundreds of changed files), full output is buffered to memory each time.
- **Files:** `python/intermap/live_changes.py:83-136`
- **Fix:** Add Go-level cache keyed on `(project, baseline, git_HEAD_sha)` with 30-second TTL. Prevents re-execution on back-to-back session calls.
- **Urgency:** Direct blocker for repeated agent use case; short TTL keeps changes visible.

### MEDIUM — IMPORTANT (Should Fix)

**C/Q3. Go replace regex matches commented-out directives (cross_project.py:101)**
- **Severity:** MEDIUM
- **Agents:** fd-correctness, fd-quality (convergence: 2/4)
- **Impact:** Pattern `r'\S+\s+=>\s+(\.\./\S+)'` matches `// replace example.com/foo => ../local-fork` inside comments, generating false dependency edges. While rare in practice, it is a correctness gap for the dependency graph.
- **Files:** `python/intermap/cross_project.py:101`
- **Fix:** Strip comment lines before matching: `non_comment = "\n".join(l for l in content.splitlines() if not l.lstrip().startswith("//"))`
- **Convergence:** Both fd-correctness and fd-quality flagged this independently.

**I-01. Stale vendor copies not deleted after extraction (vendor/workspace.py, vendor/tldrsignore.py)**
- **Severity:** MEDIUM
- **Agent:** fd-architecture
- **Impact:** After extracting `workspace.py` and `ignore.py` to top-level `python/intermap/`, the old vendor copies remain on disk. No code imports them, but the "do not modify" header adds confusion. Cleanup incomplete.
- **Files:** `python/intermap/vendor/workspace.py`, `python/intermap/vendor/tldrsignore.py`
- **Fix:** Delete both files from `vendor/`.
- **Safety:** No imports of these files outside vendor context remain (verified by fd-architecture).

**I-02 & Q2. Symbol detection misses body-only changes (live_changes.py:46-48, 47-67)**
- **Severity:** MEDIUM
- **Agents:** fd-architecture (I-02), fd-quality (Q2) — independent convergence on same issue
- **Impact:** `get_live_changes` checks `func.line_number in changed_lines` (only the `def` line). Modifications to only the function body produce empty `symbols_affected` list, defeating the annotation's purpose. The common case (edit function logic) goes undetected.
- **Files:** `python/intermap/live_changes.py:46-48`
- **Fix:** Track each symbol's line span (start→end) and check for overlap with changed lines, not just the definition line.
- **Convergence:** Two independent agents flagged this in different ways; consensus indicates real usability gap.

**P3. DefaultExtractor instantiated once but no file-level cache used (live_changes.py:26-80)**
- **Severity:** MEDIUM
- **Agent:** fd-performance
- **Impact:** Per-call DefaultExtractor re-parses AST/regex for each changed file. Python `FileCache` dies with subprocess. For 20 changed Python files called 3 times: 60 AST parses with no cross-call deduplication.
- **Files:** `python/intermap/live_changes.py:26-80`
- **Fix:** Add Go-level cache for `live_changes` results keyed on `(project, baseline, mtime_hash_of_changed_files)`.
- **Relationship:** Subsumed by P2 fix (Go-level caching).

**P4. Double ignore check per file in iter_workspace_files (workspace.py:251-282)**
- **Severity:** MEDIUM
- **Agent:** fd-performance
- **Impact:** `should_ignore` is called twice per file: once during directory pruning (line 262), once on full path after filename filter (line 276). With fnmatch fallback (when pathspec unavailable), this is O(patterns × path_components) × 2. Accumulates across full file walk in `detect_patterns`.
- **Files:** `python/intermap/workspace.py:251-282`
- **Fix:** Hoist `should_ignore` outside the inner loop or combine pruning checks; verify pathspec library is installed (faster compiled automaton).
- **Dependency:** fd-performance notes pathspec is in tldr-swinton dependency set; ensure it is loaded.

**Q1. Silent exception swallowing loses extraction errors (live_changes.py:68)**
- **Severity:** MEDIUM
- **Agent:** fd-quality
- **Impact:** `except Exception: pass` with no logging. Extraction failure (encoding issues, malformed source) returns empty `symbols_affected` with zero diagnostic signal. Caller has no way to detect that extraction was attempted and failed.
- **Files:** `python/intermap/live_changes.py:68`
- **Fix:** Add `logger` and log: `logger.debug("extraction failed for %s: %s", fpath, e)`.
- **Best Practice:** `change_impact.py` already does this correctly.

### LOW — NICE-TO-HAVE

**C-01 & Q4. Deletion hunk clamped to count=1 causes false-positive (live_changes.py:131)**
- **Severity:** LOW
- **Agents:** fd-correctness, fd-quality (convergence: 2/4)
- **Impact:** Pure-deletion hunk `@@ -5,3 +5,0 @@` has `new_count=0`. Code does `0 or 1` → `1`, so `changed_lines` includes line 5 even though no new code exists there. Symbol at line 5 spuriously appears in `symbols_affected`.
- **Files:** `python/intermap/live_changes.py:131`
- **Fix:** Separate cases: `new_count = int(raw) if raw is not None else 1` then `if new_count > 0: changed_lines.update(...)`. Remove `max(new_count, 1)` clamp.
- **Convergence:** Both correctness and quality flagged; correctness provided deep hunk-parsing analysis.

**C-02. Go replace regex matches commented-out directives (cross_project.py)**
- *See MEDIUM section; C-02 is the LOW-severity alternative analysis before the higher MEDIUM convergence finding.*

**C-03. Cobra regex misses `Use:` field after inner function brace (patterns.py:115-125)**
- **Severity:** LOW
- **Agent:** fd-correctness
- **Impact:** Pattern `[^}]*` stops at first `}`. If `Use:` appears after a function-literal closing brace within the `cobra.Command` struct, pattern misses it. Idiomatic Go places `Use:` first (low miss rate in practice), but correctness gap exists.
- **Files:** `python/intermap/patterns.py:115-125`
- **Fix:** Replace `[^}]*` with `(?:.*?)` non-greedy all-char: `r'&cobra\.Command\s*\{(?:.*?)Use:\s*"([^"]+)"'` with `re.DOTALL`.

**C-04. `open()` without encoding guard risks UnicodeDecodeError (cross_project.py:97, 116, 143)**
- **Severity:** LOW
- **Agent:** fd-correctness
- **Impact:** All three file-reading functions call `open(path)` without `encoding` or `errors` parameters. Non-UTF-8 bytes (stray encoding, corrupted file) raise unhandled `UnicodeDecodeError` that propagates up to MCP dispatcher.
- **Files:** `python/intermap/cross_project.py:97`, `116`, `143`
- **Fix:** Add `encoding="utf-8", errors="replace"` to all `open()` calls. Already done correctly in `patterns.py`.
- **Best Practice:** Bring cross_project.py to parity with patterns.py.

**I-03. cross_project_deps passes root as project positional arg (tools.go:222-224)**
- **Severity:** LOW
- **Agents:** fd-architecture (I-03), fd-quality (Q10) — quality called it INFO
- **Impact:** Call `bridge.Run(ctx, "cross_project_deps", root, map[string]any{})` reuses `project` slot for monorepo root. Semantics are correct (Python receives it as `root`) but breaks convention consistency with peer tools.
- **Files:** `internal/tools/tools.go:222-224`
- **Fix:** Pass `root` as an `args` key and use empty string for `project` positional.

**I-04 & C-05. `**_kwargs` silently absorbs `use_session=True` (change_impact.py:324)**
- **Severity:** LOW/INFO
- **Agents:** fd-architecture (I-04), fd-correctness (C-05)
- **Impact:** Removed `use_session` parameter now absorbed by `**_kwargs`. External callers passing `use_session=True` silently receive git-based results instead of session-based, with no warning. Safe by design but deserves diagnostic signal.
- **Files:** `python/intermap/change_impact.py:324`
- **Fix:** Add debug log: `if _kwargs: logger.debug("analyze_change_impact: ignoring unknown kwargs: %s", list(_kwargs))`
- **Urgency:** Low — acceptable current behavior, defensive logging improves diagnosis.

**Remaining LOW findings (cleanliness/hygiene):**
- **Q5** (fd-quality): `_detect_go_patterns` does not exclude nested vendor dirs; add comment documenting assumption or extend guard.
- **Q6** (fd-quality): Unused `from pathlib import Path` in cross_project.py; remove.
- **Q7** (fd-quality): workspace.py uses `typing.List/Union` (legacy) while new files use PEP 604/585 builtins; modernize or add note.
- **Q8** (fd-quality): Dead `if TYPE_CHECKING: pass` block in ignore.py; remove.
- **Q9** (fd-quality): Test `_init_git_repo` ignores subprocess return codes; use `check=True`.
- **P5** (fd-performance): Plugin JSON read twice per project; acceptable but design smell if pattern grows.
- **P6** (fd-performance): Re.DOTALL cobra regex is unnecessary; remove flag.

---

## Convergence Analysis

**Total Findings:** 23 (deduplicated)
**Convergence Patterns:** 2 findings reported by multiple agents

| Finding | Agents | Concordance | Interpretation |
|---------|--------|-------------|-----------------|
| Symbol detection misses body changes | I-02 (arch) + Q2 (quality) | Partial overlap, different framing | Independent discovery; real usability gap |
| Deletion hunk false positive | C-01 (correct) + Q4 (quality) | Exact same issue | Independent verification; safe to fix |
| Go replace regex false matches | C-02 (correct) + Q3 (quality) | Same root cause | Convergence validates correctness concern |
| `**_kwargs` silent absorption | I-04 (arch) + C-05 (correct) | Same code location, different severity | Low severity consensus; logging suggested |

**Unresolved Contradictions:** None. All findings are complementary or represent independent discovery of the same issue. Performance findings from fd-performance stand alone but are not contradicted by other agents.

---

## Categorized Findings

### P0 — CRITICAL (Blocks Merge/Shipping)
None.

### P1 — BLOCKING (Needs Changes Before Deploy)

1. **P1-PERF: Full file read on every tool invocation — no caching** (HIGH)
   - Fix complexity: Medium
   - Estimated effort: 2-3 hours (add mtime-keyed cache handler in tools.go)
   - Owner: fd-performance

2. **P1-PERF: Two sequential git subprocesses per call — no caching** (HIGH)
   - Fix complexity: Medium
   - Estimated effort: 2-3 hours (add Git-keyed cache in Go handler)
   - Owner: fd-performance

### P2 — IMPORTANT (Should Fix Before Deploy)

3. **P2-CORR: Go replace regex matches commented-out directives** (MEDIUM, convergence: 2/4)
   - Fix complexity: Low (one-liner: strip comments before regex)
   - Estimated effort: 30 minutes
   - Owners: fd-correctness, fd-quality

4. **P2-ARCH: Stale vendor copies not deleted after extraction** (MEDIUM)
   - Fix complexity: Trivial
   - Estimated effort: 5 minutes
   - Owner: fd-architecture

5. **P2-USAB: Symbol detection misses body-only changes** (MEDIUM, convergence: 2/4)
   - Fix complexity: Medium
   - Estimated effort: 1-2 hours (extend symbol span tracking, add test)
   - Owners: fd-architecture, fd-quality

6. **P2-PERF: DefaultExtractor no file-level cache** (MEDIUM)
   - Fix complexity: Low (subsumed by P1-PERF caching work)
   - Estimated effort: Included in P1 fix
   - Owner: fd-performance

7. **P2-PERF: Double ignore check per file** (MEDIUM)
   - Fix complexity: Low
   - Estimated effort: 30 minutes
   - Owner: fd-performance

8. **P2-QUAL: Silent exception swallowing in live_changes** (MEDIUM)
   - Fix complexity: Trivial
   - Estimated effort: 15 minutes (add logger + debug line)
   - Owner: fd-quality

### P3 — NICE-TO-HAVE (Optional Improvements)

**LOW findings (12 total):**
- Deletion hunk clamping false positive (already covered in P1-CORR via convergence)
- Cobra regex pattern blindspot (correctness gap, low miss rate in practice)
- File encoding guards (best practice alignment)
- Cross_project_deps root parameter naming (consistency)
- Various import/dead-code cleanup (workspace.py typing, ignore.py TYPE_CHECKING)
- Test subprocess error handling (CI robustness)
- Vendor directory exclusion documentation

**Info findings (2):**
- `**_kwargs` silent absorption (defensive logging suggested)
- Positional argument convention (documentation/consistency)

---

## Files Modified by Category

**Go Files:**
- `internal/tools/tools.go` — MCP tool handlers
- `internal/python/bridge.go` — subprocess invocation (no changes needed for caching; use tools.go handlers)

**Python New/Modified:**
- `python/intermap/cross_project.py` — dependency graph scanning (HIGH priority: regex fixes, encoding guards)
- `python/intermap/patterns.py` — architecture pattern detection (HIGH priority: file caching)
- `python/intermap/live_changes.py` — git-diff structural annotation (HIGH priority: caching + exception logging + symbol detection)
- `python/intermap/workspace.py` — file walking (MEDIUM priority: double-check ignore, typing modernization)
- `python/intermap/ignore.py` — ignore pattern loading (LOW: dead code cleanup)
- `python/intermap/change_impact.py` — already correct; defensive logging suggested
- `python/intermap/vendor/` — old copies should be deleted

**Test Files:**
- `python/tests/test_live_changes.py` — add body-change test case, fix subprocess error handling
- `python/tests/test_patterns.py` — (no changes noted)
- `python/tests/test_cross_project.py` — (no changes noted)

---

## Recommended Fix Priority

**Phase 1 (Blocking — do first):**
1. Add Go-level cache for detect_patterns (mtime-keyed)
2. Add Go-level cache for live_changes (git HEAD-keyed, 30s TTL)
3. Fix Go replace regex to strip comments before matching
4. Delete stale vendor copies

**Phase 2 (Important — same PR or follow-up):**
5. Fix symbol detection to check body-line overlap, not just def-line
6. Add logger + debug line to live_changes exception handler
7. Hoist ignore check outside loop in workspace.py (or verify pathspec installed)
8. Add encoding="utf-8", errors="replace" to all open() calls
9. Add test case for deletion-hunk false positive

**Phase 3 (Nice-to-have — backlog):**
10. Remove unused imports (Path from cross_project.py)
11. Modernize typing annotations in workspace.py
12. Remove dead TYPE_CHECKING block in ignore.py
13. Fix test subprocess error handling with check=True
14. Add defensive logging for **_kwargs
15. Remove re.DOTALL from cobra regex

---

## Testing Recommendations

**Before Deploy:**
- [ ] Run existing test suite: `PYTHONPATH=python python3 -m pytest python/tests/ -v`
- [ ] Add test case for deletion-hunk false positive (body-only change detection)
- [ ] Add test for regex comment stripping (go replace directive)
- [ ] Verify encoding="replace" does not silently drop legitimate content (test with non-UTF-8 manifest)
- [ ] Benchmark caching impact on large monorepo: call detect_patterns 5x, measure time reduction

**After Deploy:**
- [ ] Monitor live agent sessions for performance improvement (git subprocess caching)
- [ ] Verify symbol annotation includes body-level changes in pilot review
- [ ] Check logs for extraction failures (new debug lines in live_changes)

---

## Conclusion

The sprint is **code-ready with mandatory caching fixes**. The three new tools follow established patterns and are well-integrated. Performance degradation (HIGH findings) must be addressed before deployment to avoid session latency complaints. Symbol detection usability gap (MEDIUM, convergence: 2 agents) is worth fixing now; correctness gaps in regex patterns are low-risk but should be corrected. Vendor extraction is sound; cleanup is a 5-minute task. Estimated total fix time: 6-8 hours for all blocking + important items.

**Gate Decision:** `needs-changes` → `PASS CONDITIONAL` on completion of Phase 1 + Phase 2 items.

