#!/usr/bin/env bash
# HyUCC-Partial against a store (or a CSV export), via the SPIND->Metanome bridge.
#   ./ucc/run-multimodel.sh mongo shop orders [threshold]
#   ./ucc/run-multimodel.sh file  <path.csv> - [threshold]
set -euo pipefail
cd "$(dirname "$0")/.."
source core/adapters/build.sh
java -Xmx8g -cp "$MM_CP" HyUccMultiModel "$@"
