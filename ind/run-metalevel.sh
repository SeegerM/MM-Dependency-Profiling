#!/usr/bin/env bash
# Export the stores to the meta level (one observation set per scope), then run SPIND over it.
#   ./ind/run-metalevel.sh [threshold]
set -euo pipefail
cd "$(dirname "$0")/.."
source config/paths.env
THRESHOLD="${1:-1.0}"
EXPORT="$WORK_ROOT/unibench/metalevel"

SPIND_W=$(cd "$SPIND_HOME" && pwd -W)
CP="$(pwd -W)/core/metalevel/build;$(pwd -W)/ind/build;$SPIND_W/target/classes;$(cat "$SPIND_HOME/cp.txt")"
mkdir -p core/metalevel/build ind/build
javac -nowarn -cp "$CP" -d core/metalevel/build core/metalevel/src/*.java
javac -nowarn -cp "$CP" -d ind/build ind/src/SpindMetaLevel.java

if [ ! -f "$EXPORT/catalog.json" ] || [ "${2:-}" = "--reexport" ]; then
  java -Dspind.connections="$(pwd -W)/config/connections.unibench.properties" -cp "$CP" \
    ExportUniBench "$EXPORT"
fi
java -Xmx8g -cp "$CP" SpindMetaLevel "$EXPORT" "$THRESHOLD" \
  "$WORK_ROOT/unibench/temp" "$WORK_ROOT/unibench/meta-results-$THRESHOLD"
