#!/usr/bin/env bash
# Thin wrapper around interbump.sh for intermap
set -euo pipefail
exec "$(dirname "$0")/../../scripts/interbump.sh" "$@"
