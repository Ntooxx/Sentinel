#!/usr/bin/env bash
# Scan Sentinel itself.

set -e
cd "$(dirname "$0")/.."
project-sentinel scan . --format text
