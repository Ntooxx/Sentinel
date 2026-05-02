#!/usr/bin/env bash
# Scan a project and generate an HTML report

TARGET="${1:-.}"
cd "$(dirname "$0")/.."
echo "=== Scanning: $TARGET ==="
python sentinel.py scan "$TARGET" --fast
echo ""
echo "=== Generating HTML Report ==="
python sentinel.py report "$TARGET" --format html
echo ""
echo "Report saved to SENTINEL_REPORT.html"
