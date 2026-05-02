#!/usr/bin/env bash
# Scan a real-world framework (FastAPI) to see Sentinel in action

set -e
REPO_DIR="/tmp/sentinel-fastapi-benchmark"
FASTAPI_URL="https://github.com/fastapi/fastapi.git"

cd "$(dirname "$0")/.."

if [ ! -d "$REPO_DIR" ]; then
    echo "=== Cloning FastAPI ==="
    git clone --depth=1 "$FASTAPI_URL" "$REPO_DIR"
fi

echo "=== Scanning FastAPI ==="
project-sentinel scan "$REPO_DIR" --fast

echo ""
echo "=== Generating FastAPI Report ==="
project-sentinel report "$REPO_DIR" --format html

echo ""
echo "=== FastAPI Overview ==="
project-sentinel overview "$REPO_DIR" --fast --quiet
