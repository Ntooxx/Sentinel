#!/usr/bin/env bash
# Scan Sentinel itself

cd "$(dirname "$0")/.."
python sentinel.py scan . --fast
