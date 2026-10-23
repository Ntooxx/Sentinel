#!/usr/bin/env bash
# Keep a live, self-contained HTML report while editing.
# Sentinel 2.0 has no dashboard server; this is the replacement.

set -e
TARGET="${1:-.}"
cd "$(dirname "$0")/.."
echo "=== Watching: $TARGET ==="
echo "Open .sentinel/quality.html in your browser (Ctrl+C to stop)."
project-sentinel watch "$TARGET" --out .sentinel/quality.html
