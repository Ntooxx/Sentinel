#!/usr/bin/env bash
# Run reproducible benchmark across all bundled fixture repos

cd "$(dirname "$0")/.."
echo "=== Sentinel Benchmark ==="
project-sentinel benchmark . --fast
