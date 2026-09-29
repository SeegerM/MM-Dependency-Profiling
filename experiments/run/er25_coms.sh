#!/usr/bin/env bash
# Runs the ER'25 framework and ours on the same MMSBench-Net instance.
#
#   ./experiments/run/er25_coms.sh [networkScale]
#
# ER'25 reads everything behind a database URI, so it cannot share the main stack with the other
# corpora. This script builds three throwaway stores on the extractor's Docker network holding only
# this scenario, and points both systems at them.
#
# Extractor settings:
#   * Mongo is unauthenticated: ER'25 derives pymongo's authSource from the URI's database, so a
#     root user in `admin` cannot be used.
#   * The image is the local build with the UACFinder guard from apply-patches.py; without it the
#     job aborts on this corpus.
#   * find_max_ind is off, so both systems compute unary inclusions; the exhaustive maximal-IND
#     search runs out of memory here.
set -uo pipefail
cd "$(dirname "$0")/../.."
source core/adapters/build.sh
source config/env.sh

SCALE="${1:-3}"
NET=schema-extraction-network
CONN="$MM_REPO/config/connections.coms.properties"
OUT="$WORK_ROOT/coms-iso"
EXPORT="$OUT/metalevel"
SETTINGS="$OUT/settings-unary.yaml"

if [ -z "${COMS_CP:-}" ]; then
  echo "the Polypheny benchmarking client is not built; see POLY_HOME in config/env.sh" >&2
  exit 1
fi
SPIND_W="$SPIND_HOME"
CP="$(cpjoin "$MM_REPO/core/metalevel/build" "$SPIND_W/target/classes" "$(cat "$SPIND_HOME/cp.txt")" "$COMS_CP")"
mkdir -p "$OUT/er25"

echo "=== isolated stores on $NET"
for name in coms-postgres coms-mongo coms-neo4j; do docker rm -f "$name" >/dev/null 2>&1; done
docker run -d --name coms-postgres --network $NET -p 5433:5432 \
  -e POSTGRES_USER=spind -e POSTGRES_PASSWORD=spindpass -e POSTGRES_DB=coms postgres:17 >/dev/null
docker run -d --name coms-mongo --network $NET -p 27018:27017 mongo:8 >/dev/null
docker run -d --name coms-neo4j --network $NET -p 7688:7687 -p 7475:7474 \
  -e NEO4J_AUTH=neo4j/testpassword neo4j:2026.03.1 >/dev/null
echo "  waiting for the stores"
sleep 45

echo "=== load and export the instance both systems will read"
java -cp "$CP" -Dspind.connections="$CONN" LoadComs "$SCALE" 2>/dev/null | grep -E "^  |^loaded"
rm -rf "$EXPORT"
# Timed separately: this is the counterpart to ER'25's data-import phase.
export_start=$(date +%s%3N)
java -cp "$CP" -Dspind.connections="$CONN" ExportComs \
  "$(mm_path "$EXPORT")" 2>/dev/null | tail -2
echo "$(( $(date +%s%3N) - export_start ))" > "$OUT/export-ms.txt"
echo "  export $(cat "$OUT/export-ms.txt")ms"

echo "=== our unary discovery"
rm -rf "$OUT/meta-results-1.0"
start=$(date +%s%3N)
java -Xmx8g -cp "$MM_CP" SpindMetaLevel \
  "$(mm_path "$EXPORT")" 1.0 \
  "$OUT/temp" "$OUT/meta-results-1.0" < /dev/null > "$OUT/ind.log" 2>&1
echo "  $(( $(date +%s%3N) - start ))ms  $(grep -oE 'Found [0-9]+ pINDs' "$OUT/ind.log" | tail -1)"

echo "=== ER'25"
sed 's/find_max_ind: !!bool true/find_max_ind: !!bool false/' \
  upstream/ref-seeker-extractor/ref-seeker-extractor/settings.yaml > "$SETTINGS"
docker rm -f ref-seeker-extractor-ref-seeker-extractor-1 >/dev/null 2>&1
docker run -d --name ref-seeker-extractor-ref-seeker-extractor-1 --network $NET -p 8001:8001 \
  -v "$(mm_path "$SETTINGS")":/code/settings.yaml:ro ref-seeker-extractor:local >/dev/null
echo "DROP DATABASE IF EXISTS refseeker;CREATE DATABASE refseeker;" \
  | docker exec -i ref-seeker-extractor-mariadb-1 mariadb --user=root --password=refseeker 2>/dev/null
sleep 25

REQUEST='{"entries":[
 {"uri":"postgresql://coms-postgres:5432/coms","user":"spind","password":"spindpass"},
 {"uri":"mongodb://coms-mongo:27017/coms","user":"","password":""},
 {"uri":"neo4j://coms-neo4j:7687","user":"neo4j","password":"testpassword"}]}'
start=$(date +%s%3N)
JOB=$(curl -s -m 60 -X POST http://localhost:8001/extract -H "Content-Type: application/json" \
  -d "$REQUEST" | python -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
echo "  job $JOB"
for _ in $(seq 1 45); do
  STATUS=$(curl -s -m 20 "http://localhost:8001/jobs/$JOB" 2>/dev/null)
  case "$STATUS" in
    *running*|*waiting*) sleep 20 ;;
    *) break ;;
  esac
done
echo "  finished in $(( $(date +%s%3N) - start ))ms with status ${STATUS:-unknown}"
# /jobs/<id>/results carries ER'25's per-phase runtime_metrics; /jobs/<id> returns only a status.
mkdir -p "$OUT/er25"
echo "$JOB" > "$OUT/er25/job-id.txt"
curl -s -m 120 "http://localhost:8001/jobs/$JOB/results" -o "$OUT/er25/results.json" 2>/dev/null
# `aborted` is the expected outcome: discovery completes and the foreign-key ranker then fails on
# this corpus's type-heterogeneous document keys. The inclusion and UAC tables are already written.

echo "=== collect its results"
mariadb_query() {
  echo "$1" | docker exec -i ref-seeker-extractor-mariadb-1 \
    mariadb --user=root --password=refseeker refseeker -B 2>/dev/null
}
mariadb_query "select i.id, i.child_server_type, i.child_db_name, i.child_datastorage_id, i.child_datastorage_name, i.child_attribute_names, u.server_type, u.db_name, u.datastorage_id, u.datastorage_name, u.attribute_names, i.HoPF_probability, i.hybrid_probability from inclusion_dependencies i join unique_attributecombinations u on u.id = i.UAC_id order by i.id;" > "$OUT/er25/inds.tsv"
mariadb_query "select id, storage_name, db_id, parent_id from datastorage order by id;" > "$OUT/er25/datastorage.tsv"
mariadb_query "select u.server_type, u.datastorage_name, u.attribute_names, u.pk_score_hopf from unique_attributecombinations u order by u.id;" > "$OUT/er25/uacs.tsv"
echo "  inclusions $(( $(wc -l < "$OUT/er25/inds.tsv") - 1 )), datastorages $(( $(wc -l < "$OUT/er25/datastorage.tsv") - 1 ))"

python experiments/report/er25_coms.py
