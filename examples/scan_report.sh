#!/usr/bin/env bash
# Scan a project and generate a self-contained HTML report.

set -e
TARGET="${1:-.}"
cd "$(dirname "$0")/.."
echo "=== Scanning: $TARGET ==="
project-sentinel scan "$TARGET" --format html --out .sentinel/quality.html --quiet
echo ""
echo "Report written to .sentinel/quality.html"
