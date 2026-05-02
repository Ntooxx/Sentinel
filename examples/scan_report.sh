#!/usr/bin/env bash
# Scan a project and generate an HTML report

TARGET="${1:-.}"
cd "$(dirname "$0")/.."
echo "=== Scanning: $TARGET ==="
project-sentinel scan "$TARGET" --fast
echo ""
echo "=== Generating HTML Report ==="
project-sentinel report "$TARGET" --format html
echo ""
echo "Report saved to SENTINEL_REPORT.html"
