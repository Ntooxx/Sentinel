#!/usr/bin/env bash
# Scan Sentinel itself

cd "$(dirname "$0")/.."
project-sentinel scan . --fast
