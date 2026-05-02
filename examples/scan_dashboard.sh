#!/usr/bin/env bash
# Launch the Sentinel dashboard

TARGET="${1:-.}"
cd "$(dirname "$0")/.."
echo "=== Dashboard for: $TARGET ==="
echo "Open http://127.0.0.1:8765 in your browser"
python sentinel.py dashboard "$TARGET" --fast
