#!/usr/bin/env bash
# Scan a large JavaScript/TypeScript repository with conservative heuristics.

set -e
REPO_DIR="/tmp/sentinel-react-demo"
REACT_URL="https://github.com/facebook/react.git"

cd "$(dirname "$0")/.."

if [ ! -d "$REPO_DIR" ]; then
    echo "=== Cloning React ==="
    git clone --depth=1 "$REACT_URL" "$REPO_DIR"
fi

echo "=== Scanning React ==="
project-sentinel scan "$REPO_DIR" --format text
