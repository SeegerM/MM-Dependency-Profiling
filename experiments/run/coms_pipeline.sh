#!/usr/bin/env bash
# Loads MMSBench-Net into the three stores and runs the full profile pipeline over it.
#
#   ./experiments/run/coms_pipeline.sh [networkScale]
#
# MMSBench-Net ships as a scenario of the Polypheny benchmarking client. LoadComs uses only its
# simulation, so the network is loaded into PostgreSQL, MongoDB and Neo4j and read back through the
# ordinary meta-level export, keeping the benchmark's own store assignment.
#
# Everything this scenario writes is namespaced: schema `coms` in PostgreSQL, database `coms` in
# MongoDB, and the `coms_` label prefix in Neo4j. Rerunning drops and rewrites only those.
set -uo pipefail
cd "$(dirname "$0")/../.."
source core/adapters/build.sh
source config/env.sh

SCALE="${1:-3}"
if [ -z "${COMS_CP:-}" ]; then
  echo "the Polypheny benchmarking client is not built; see POLY_HOME in config/env.sh" >&2
  exit 1
fi
# Narrower than MM_CP on purpose: the loader and the export need SPIND and the benchmark client,
# not the Metanome jars, and the two concatenated overflow the Windows command line.
SPIND_W="$SPIND_HOME"
CP="$(cpjoin "$MM_REPO/core/metalevel/build" "$SPIND_W/target/classes" "$(cat "$SPIND_HOME/cp.txt")" "$COMS_CP")"

OUT="$WORK_ROOT/coms"
EXPORT="$OUT/metalevel"
mkdir -p "$OUT"

echo "=== load (network scale $SCALE)"
java -cp "$CP" LoadComs "$SCALE" 2> "$OUT/load.err" | grep -E "^generated|^  |^loaded"

echo "=== export"
rm -rf "$EXPORT"
java -cp "$CP" ExportComs "$(mm_path "$EXPORT")" \
  2> "$OUT/export.err" | tail -2

echo "=== inclusions"
for rho in 1.0 0.95; do
  rm -rf "$OUT/meta-results-$rho"
  java -Xmx8g -cp "$MM_CP" SpindMetaLevel \
    "$(mm_path "$EXPORT")" "$rho" \
    "$OUT/temp" "$OUT/meta-results-$rho" < /dev/null > "$OUT/ind-$rho.log" 2>&1
  echo "  rho=$rho $(grep -oE 'Found [0-9]+ ps?INDs' "$OUT/ind-$rho.log" | tail -1)"
done

echo "=== similarity"
rm -rf "$OUT/sim"
java -Xmx8g -cp "$MM_CP" SpindSimilarity \
  "$(mm_path "$EXPORT")" 0.95 1 \
  "$OUT/temp" "$OUT/sim" < /dev/null > "$OUT/sim.log" 2>&1
echo "  $(grep -oE 'Found [0-9]+ ps?INDs' "$OUT/sim.log" | tail -1)"

# FDs and UCCs first: hp_records reads their dumps, and genuineness_sweep is what produces them.
echo "=== FDs and UCCs"
python experiments/run/genuineness_sweep.py "$EXPORT" --threshold=1.0 --top=0 \
  --json="$OUT/genuineness-fducc.json" 2>&1 | grep -E "^coms:|written"

echo "=== inclusion genuineness"
python profile/genuineness.py "$EXPORT" "$OUT/meta-results-1.0" \
  --json="$OUT/genuineness.json" --top=0 2>&1 | grep -E "^background|written"

echo "=== profile and reports"
python experiments/report/hp_records_report.py
python experiments/report/coms_taxonomy.py
