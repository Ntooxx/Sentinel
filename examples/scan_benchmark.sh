#!/usr/bin/env bash
# Run the reproducible benchmark across a repository.

set -e
cd "$(dirname "$0")/.."
python tools/benchmark.py --repo "${1:-.}" --mode all
