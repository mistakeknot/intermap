---
title: "Sprint Reflect: Intermap Project-Level Code Mapping"
bead: iv-w7bh
category: sprint-reflect
date: 2026-02-23
tags: [mcp-tools, vendor-extraction, go-python-bridge, quality-gates]
---

# Sprint Reflect: iv-w7bh

## What Was Built

- 3 new MCP tools: `cross_project_deps`, `detect_patterns`, `live_changes`
- Vendor extraction: `workspace.py` and `ignore.py` promoted from vendor to owned modules
- Vision and roadmap docs for intermap
- Total: 9 MCP tools registered (was 6)

## Key Patterns Discovered

### 1. Go MCP Tool Registration Pattern (P0 blocker)

The plan originally had the wrong Go registration pattern. mcp-go v0.43.2 uses
`server.ServerTool` factory functions, NOT `server.Tool()` constructors. This was
caught in plan review (amendment #1) and would have been a compile-time failure.

**Lesson:** Always verify framework API against the actual codebase before writing
plan code snippets. The plan review step (flux-drive on the plan) caught this before
execution — validating the gate.

### 2. Python Truthiness Trap in Hunk Parsing (C-01)

`int("0") or 1` evaluates to `1` because `0` is falsy in Python. This caused
pure-deletion hunks (`@@ -5,3 +5,0 @@`) to incorrectly mark line 5 as changed.

**Fix pattern:** When parsing optional numeric fields, always use explicit `None`
checks: `int(raw) if raw is not None else default` — never use `or` with integers.

### 3. Vendor Extraction Checklist

Extracting modules from a vendor directory requires:
1. Copy and rename file
2. Update header comment (remove "do not modify")
3. Fix internal imports (`.tldrsignore` → `.ignore`)
4. Update all consumers (`from .vendor.X` → `from .X`)
5. **Delete stale originals** (missed initially, caught by architecture review)
6. Update CLAUDE.md vendor section

Step 5 was missed during execution and caught by quality gates (I-01).

### 4. Regex Matching in Manifest Files

Go.mod comment lines (`// replace ...`) can produce false dependency edges.
Always strip comments before regex matching on structured files.

Similarly, `[^}]*` in struct-matching regexes stops at the first `}`, which
fails when inner function literals contain `}`. Use `(?:.*?)` with DOTALL instead.

### 5. Go Bridge Convention

The `cross_project_deps` tool breaks convention by passing `root` (monorepo root)
where other tools pass `project` (project dir). While semantically correct on the
Python side, the inconsistency at the Go call site (`bridge.Run(ctx, cmd, root, ...)`)
makes auditing harder. Documented as a known deviation.

## Quality Gate Effectiveness

4 review agents ran in parallel:
- **fd-architecture:** Caught stale vendor copies, symbol detection limitation
- **fd-correctness:** Caught the int truthiness bug, regex blind spots, encoding gaps
- **fd-quality:** Caught silent exception swallowing, dead code, style inconsistencies
- **fd-performance:** Identified caching gaps (deferred — Go-level cache is follow-on work)

All 4 returned `needs-changes`. 8 findings were resolved in a single commit.
2 findings deferred (Go-level caching for new tools, symbol body-range detection).

## Deferred Work

- **Go-level LRU cache for new tools** (P1/P2 from performance review): The Go cache
  infrastructure exists but new tools bypass it. Each Python subprocess dies on exit,
  so the Python FileCache is per-call. Adding mtime-keyed Go cache entries would
  eliminate re-scans.
- **Symbol body-range detection** (I-02/Q2): `live_changes` only matches symbols
  whose `def` line is in changed hunks. Body-only edits are missed. Requires
  tracking symbol end-line spans.
- **Cobra regex robustness** (C-03): `Use:` field after function-literal inner braces
  is missed. Low impact (idiomatic Go puts `Use:` first).

## Complexity Calibration

Estimated: C3 (moderate). Actual: C3 — the plan review amendments (16 total) were
significant but all addressable. The Go registration pattern P0 would have been a
showstopper without the plan review gate.
