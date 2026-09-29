#!/usr/bin/env bash
# HyFD-Partial against a store, via the SPIND->Metanome bridge.
#   ./fd/run-multimodel.sh postgres public customer [threshold]
set -euo pipefail
cd "$(dirname "$0")/.."
source core/adapters/build.sh
java -Xmx8g -cp "$MM_CP" HyFdMultiModel "$@"
