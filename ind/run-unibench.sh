#!/usr/bin/env bash
# SPIND over the UniBench-IND three-store setup.
#   ./ind/run-unibench.sh [threshold]
set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh
THRESHOLD="${1:-1.0}"
SPIND_W="$SPIND_HOME"
CP="$(cpjoin "$MM_REPO/ind/build" "$SPIND_W/target/classes" "$(cat "$SPIND_HOME/cp.txt")")"
mkdir -p ind/build
javac -nowarn -cp "$CP" -d ind/build ind/src/SpindMultiModel.java
java -Xmx8g -Dspind.connections="$MM_REPO/config/connections.unibench.properties" -cp "$CP" \
  SpindMultiModel "$THRESHOLD" "$WORK_ROOT/unibench/temp" "$WORK_ROOT/unibench/results-$THRESHOLD"
