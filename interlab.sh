#!/usr/bin/env bash
set -euo pipefail
# intermap/interlab.sh — wraps Go benchmarks for interlab.
# Primary: pattern_detect_ns (BenchmarkDetectPatterns_Warm)

HARNESS="${INTERLAB_HARNESS:-$(git rev-parse --show-toplevel)/interverse/interlab/scripts/go-bench-harness.sh}"
DIR="$(cd "$(dirname "$0")" && pwd)"

# Suppress Python site-packages noise (matplotlib pth stderr) that pollutes go test output
export PYTHONNOUSERSITE=1

bash "$HARNESS" --pkg ./internal/tools/ --bench 'BenchmarkDetectPatterns_Warm$' --metric pattern_detect_ns --dir "$DIR"
