#!/usr/bin/env bash
# Times the whole multi-model pipeline as the instance grows.
#
#   ./experiments/run/scale_multimodel.sh [orders...]
#
# The performance argument in the paper is about multi-model workloads -- extractor-rich relative
# to their data volume -- while the scaling numbers reported so far come from single-model
# relational corpora. This measures the setting the argument is about.
#
# Each phase is timed separately on purpose. Export is now the mandatory path for document and
# graph stores, so if it dominates, the cost has been moved rather than removed and the paper
# should say so.
set -uo pipefail
cd "$(dirname "$0")/../.."
source core/adapters/build.sh
source config/paths.env

SIZES=("$@")
[ ${#SIZES[@]} -eq 0 ] && SIZES=(10000 20000 40000 80000)
OUT="$WORK_ROOT/scale"
RESULTS="$OUT/timings.jsonl"
mkdir -p "$OUT"

ms() { date +%s%3N; }

for orders in "${SIZES[@]}"; do
  DATA="$OUT/o$orders"
  EXPORT="$DATA/metalevel"
  echo "=== $orders orders"

  python experiments/datasets/build_dataset.py "--orders=$orders" "--out=$(cygpath -m "$DATA" 2>/dev/null || echo "$DATA")" > "$OUT/gen-$orders.log" 2>&1
  bash experiments/datasets/load_stores.sh "$DATA" > "$OUT/load-$orders.log" 2>&1

  t0=$(ms)
  java -cp "$MM_CP" -Dspind.connections="$(pwd -W)/config/connections.properties" \
       ExportTpch "$(cygpath -m "$EXPORT" 2>/dev/null || echo "$EXPORT")" \
       > "$OUT/export-$orders.log" 2>&1
  t_export=$(( $(ms) - t0 ))

  t0=$(ms)
  java -Xmx8g -cp "$MM_CP" SpindMetaLevel "$EXPORT" 0.95 "$OUT/temp" "$OUT/ind-$orders" \
       < /dev/null > "$OUT/ind-$orders.log" 2>&1
  t_ind=$(( $(ms) - t0 ))

  t0=$(ms)
  java -Xmx8g -cp "$MM_CP" SpindSimilarity "$EXPORT" 0.95 1 "$OUT/temp" "$OUT/sim-$orders" \
       < /dev/null > "$OUT/sim-$orders.log" 2>&1
  t_sim=$(( $(ms) - t0 ))

  # FDs and UCCs are per observation set, so their cost is the sum over the catalog.
  t_fd=0; t_ucc=0
  mapfile -t FILES < <(python experiments/lib/list_export.py "$EXPORT" | tr -d '\r')
  for file in "${FILES[@]}"; do
    t0=$(ms)
    java -Xmx8g -cp "$MM_CP" HyFdMultiModel file "$file" - 1.0 < /dev/null > /dev/null 2>&1
    t_fd=$(( t_fd + $(ms) - t0 ))
    t0=$(ms)
    java -Xmx8g -cp "$MM_CP" HyUccMultiModel file "$file" - 1.0 < /dev/null > /dev/null 2>&1
    t_ucc=$(( t_ucc + $(ms) - t0 ))
  done

  rows=$(python -c "import json,sys; print(sum(e['rows'] for e in json.load(open(sys.argv[1]))))" "$EXPORT/catalog.json")
  attrs=$(python -c "import json,sys; print(sum(e['columns'] for e in json.load(open(sys.argv[1]))))" "$EXPORT/catalog.json")
  inds=$(grep -oE "Found [0-9]+ pINDs" "$OUT/ind-$orders.log" | tail -1 | grep -oE "[0-9]+")

  echo "{\"orders\":$orders,\"rows\":$rows,\"extractors\":$attrs,\"inds\":${inds:-0},\
\"export_ms\":$t_export,\"ind_ms\":$t_ind,\"similarity_ms\":$t_sim,\"fd_ms\":$t_fd,\"ucc_ms\":$t_ucc}" \
    >> "$RESULTS"
  echo "  export ${t_export}ms  ind ${t_ind}ms  sim ${t_sim}ms  fd ${t_fd}ms  ucc ${t_ucc}ms"
done

echo
echo "timings -> $RESULTS"
