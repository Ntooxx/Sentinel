#!/usr/bin/env bash
# Scan FastAPI source + tests and write a one-page HTML report.

set -e
REPO_DIR="/tmp/sentinel-fastapi-benchmark"
FASTAPI_URL="https://github.com/fastapi/fastapi.git"

cd "$(dirname "$0")/.."

if [ ! -d "$REPO_DIR" ]; then
    echo "=== Cloning FastAPI ==="
    git clone --depth=1 "$FASTAPI_URL" "$REPO_DIR"
fi

echo "=== Scanning FastAPI ==="
project-sentinel scan "$REPO_DIR" --format html --out "$REPO_DIR/.sentinel/quality.html" --quiet

echo ""
echo "Report written to $REPO_DIR/.sentinel/quality.html"
