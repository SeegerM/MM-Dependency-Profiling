#!/usr/bin/env bash
# Runs the inter-model FD pipeline on every instance that has two stores, in one pass.
#
#   ./experiments/run/intermodel_all.sh [u]
#
# Each instance is (label, export, IND results). The IND result directory is the run reported for
# that instance elsewhere in the evaluation, at that instance's own threshold.
set -uo pipefail
cd "$(dirname "$0")/../.."
source core/adapters/build.sh
source config/env.sh

U="${1:-0.1}"
W="$WORK_ROOT"

run() {
  local label="$1" instance="$2" results="$3"
  local export="$W/$instance/metalevel"
  if [ ! -f "$export/catalog.json" ] || [ ! -d "$W/$results" ]; then
    echo "  $label: export or result set not present, skipped"
    return
  fi
  python experiments/run/intermodel_pipeline.py "$export" "$W/$results" --u="$U" --sweep \
    --json="$W/$instance/pipeline.json" > "$W/$instance/pipeline.txt" 2>&1
  echo "  $label: $(grep -E 'inter-model FDs at u ' "$W/$instance/pipeline.txt" | tail -1)"
}

echo "=== inter-model FD pipeline at u = $U"
run "TPC-H"           mm_tpch      "compare/mm-1.0/ind-results"
run "UniBench"        unibench     "unibench/meta-results-0.5"
run "IMDB"            imdb         "imdb/eq-1.0-n1"
run "MusicBrainz"     musicbrainz  "musicbrainz/eq-1.0-n1"
run "MovieLens"       movielens    "movielens/meta-results-0.5"
run "Helsinki Patent" hpatent      "hpatent/meta-results-0.5"
run "Helsinki Person" hperson      "hperson/meta-results-0.5/ind-results"
run "Helsinki Film"   hfilm        "hfilm/meta-results-0.5/ind-results"
run "ListenBrainz"    listenbrainz "listenbrainz/meta-results-0.5"
run "MMSBench-Net"    coms         "coms/meta-results-1.0"
echo "INTERMODEL ALL DONE"
