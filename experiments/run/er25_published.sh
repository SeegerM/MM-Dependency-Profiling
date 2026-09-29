#!/usr/bin/env bash
# Runs ER'25 and this work on the three published instances, on the same reduced data.
#
#   ./experiments/run/er25_published.sh [rows]
#
# ER'25 materialises one row per value into its own relational meta level, which does not scale to
# these instances (three to ten million observations). Every source is truncated to the same row cap
# and both systems run on the truncated instance.
#
# The stores are isolated on the extractor's Docker network, one corpus at a time, because ER'25
# reads everything behind a database URI and cannot be pointed at a schema.
set -uo pipefail
cd "$(dirname "$0")/../.."
source core/adapters/build.sh
source config/env.sh

ROWS="${1:-20000}"
NET=schema-extraction-network
export PG_CONTAINER=coms-postgres MONGO_CONTAINER=coms-mongo NEO4J_CONTAINER=coms-neo4j
export MONGO_AUTH=""
SETTINGS="$WORK_ROOT/er25-unary-settings.yaml"

echo "=== isolated stores"
for name in coms-postgres coms-mongo coms-neo4j; do docker rm -f "$name" >/dev/null 2>&1; done
docker run -d --name coms-postgres --network $NET -p 5433:5432 \
  -e POSTGRES_USER=spind -e POSTGRES_PASSWORD=spindpass -e POSTGRES_DB=postgres postgres:17 >/dev/null
docker run -d --name coms-mongo --network $NET -p 27018:27017 mongo:8 >/dev/null
docker run -d --name coms-neo4j --network $NET -p 7688:7687 -p 7475:7474 \
  -e NEO4J_AUTH=neo4j/testpassword neo4j:2026.03.1 >/dev/null
sleep 45

# find_max_ind off: the exhaustive maximal-IND search does not finish at this attribute count, and
# both systems then compute unary inclusions.
sed 's/find_max_ind: !!bool true/find_max_ind: !!bool false/' \
  upstream/ref-seeker-extractor/ref-seeker-extractor/settings.yaml > "$SETTINGS"
docker rm -f ref-seeker-extractor-ref-seeker-extractor-1 >/dev/null 2>&1
docker run -d --name ref-seeker-extractor-ref-seeker-extractor-1 --network $NET -p 8001:8001 \
  -v "$(mm_path "$SETTINGS")":/code/settings.yaml:ro ref-seeker-extractor:local >/dev/null
sleep 25

for instance in movielens hpatent listenbrainz; do
  echo
  echo "############ $instance (capped at $ROWS rows per source)"
  SMALL="$WORK_ROOT/$instance-small"
  python experiments/datasets/truncate_instance.py "$WORK_ROOT/$instance" "$SMALL" "$ROWS"

  export PG_DATABASE="$instance"
  docker exec "$PG_CONTAINER" psql -U spind -d postgres -c "DROP DATABASE IF EXISTS $instance;" >/dev/null 2>&1
  docker exec "$PG_CONTAINER" psql -U spind -d postgres -c "CREATE DATABASE $instance;" >/dev/null 2>&1
  # Neo4j has one database, so the previous corpus's nodes have to go before the next arrives.
  docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword \
    "MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 20000 ROWS;" >/dev/null 2>&1

  case "$instance" in
    movielens)    bash experiments/datasets/load_movielens.sh "$SMALL" ;;
    hpatent)      bash experiments/datasets/load_helsinki_patent.sh "$SMALL" ;;
    listenbrainz) bash experiments/datasets/load_listenbrainz.sh "$SMALL" ;;
  esac > "$SMALL/load.log" 2>&1
  echo "  loaded"

  echo "  our discovery"
  rm -rf "$SMALL/metalevel" "$SMALL/meta-results-0.5"
  mkdir -p "$SMALL/metalevel"
  # One connections file per instance: the isolated PostgreSQL holds a database per corpus, so the
  # export has to be pointed at the one the loader just filled rather than at a fixed name.
  CONN="$SMALL/connections.properties"
  {
    echo "postgres.pg.url=jdbc:postgresql://localhost:5433/$instance"
    echo "postgres.pg.user=spind"
    echo "postgres.pg.password=spindpass"
    echo "mongo.mongo.uri=mongodb://localhost:27018"
    echo "neo4j.graph.uri=bolt://localhost:7688"
    echo "neo4j.graph.user=neo4j"
    echo "neo4j.graph.password=testpassword"
  } > "$CONN"
  # Timed separately: this is the counterpart to ER'25's data-import phase.
  export_start=$(date +%s%3N)
  java -Xmx12g -cp "$MM_CP" -Dspind.connections="$(mm_path "$CONN")" \
    ExportSources "$MM_REPO/experiments/datasets/specs/$instance.sources" \
    "$(mm_path "$SMALL/metalevel")" > "$SMALL/export.log" 2>&1
  echo "$(( $(date +%s%3N) - export_start ))" > "$SMALL/export-ms.txt"
  echo "    export $(cat "$SMALL/export-ms.txt")ms"
  start=$(date +%s%3N)
  java -Xmx12g -cp "$MM_CP" SpindMetaLevel "$(mm_path "$SMALL/metalevel")" 0.5 \
    "$SMALL/temp" "$SMALL/meta-results-0.5" < /dev/null > "$SMALL/ind.log" 2>&1
  echo "    $(( $(date +%s%3N) - start ))ms $(grep -oE 'Found [0-9]+ pINDs' "$SMALL/ind.log" | tail -1)"
  python experiments/run/verify_degrees.py "$SMALL/metalevel" "$SMALL/meta-results-0.5" \
    --quiet --fix 2>&1 | tail -1

  echo "  ER'25"
  echo "DROP DATABASE IF EXISTS refseeker;CREATE DATABASE refseeker;" \
    | docker exec -i ref-seeker-extractor-mariadb-1 mariadb --user=root --password=refseeker 2>/dev/null
  # MovieLens's document half is TMDB's export and lives in `tmdb`. Helsinki has no document half,
  # so its entry is omitted.
  case "$instance" in
    movielens)    MONGO_DB=tmdb ;;
    listenbrainz) MONGO_DB=listenbrainz ;;
    *)            MONGO_DB="" ;;
  esac
  MONGO_ENTRY=""
  if [ -n "$MONGO_DB" ]; then
    MONGO_ENTRY="{\"uri\":\"mongodb://coms-mongo:27017/$MONGO_DB\",\"user\":\"\",\"password\":\"\"},"
  fi
  REQUEST="{\"entries\":[
    {\"uri\":\"postgresql://coms-postgres:5432/$instance\",\"user\":\"spind\",\"password\":\"spindpass\"},
    $MONGO_ENTRY
    {\"uri\":\"neo4j://coms-neo4j:7687\",\"user\":\"neo4j\",\"password\":\"testpassword\"}]}"
  start=$(date +%s%3N)
  JOB=$(curl -s -m 60 -X POST http://localhost:8001/extract -H "Content-Type: application/json" \
    -d "$REQUEST" | python -c "import json,sys; print(json.load(sys.stdin).get('job_id',''))")
  for _ in $(seq 1 90); do
    STATUS=$(curl -s -m 20 "http://localhost:8001/jobs/$JOB" 2>/dev/null)
    case "$STATUS" in *running*|*waiting*) sleep 20 ;; *) break ;; esac
  done
  echo "    $(( $(date +%s%3N) - start ))ms status ${STATUS:-unreachable}"

  mkdir -p "$SMALL/er25"
  # /jobs/<id>/results carries ER'25's per-phase runtime_metrics; /jobs/<id> returns only a status.
  # The job id is kept so a run can be re-queried.
  echo "$JOB" > "$SMALL/er25/job-id.txt"
  curl -s -m 120 "http://localhost:8001/jobs/$JOB/results" -o "$SMALL/er25/results.json" 2>/dev/null
  python - "$SMALL/er25/results.json" <<'CHECK'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    blob = json.loads(path.read_text(encoding="utf-8"))
    schemas = blob.get("json_schema") or []
    metrics = (schemas[0].get("runtime_metrics") if schemas else None) or {}
    print("    runtime_metrics: overall %.1fs, import %.1fs, unary IND %.2fs"
          % (metrics.get("overall_time", -1), metrics.get("time_data_import", -1),
             (metrics.get("time_INDFinder") or {}).get("time_find_unary", -1)))
except Exception as error:
    print("    runtime_metrics NOT saved: %s" % error)
CHECK
  q() { echo "$1" | docker exec -i ref-seeker-extractor-mariadb-1 \
          mariadb --user=root --password=refseeker refseeker -B 2>/dev/null; }
  q "select i.id, i.child_server_type, i.child_db_name, i.child_datastorage_id, i.child_datastorage_name, i.child_attribute_names, u.server_type, u.db_name, u.datastorage_id, u.datastorage_name, u.attribute_names, i.HoPF_probability, i.hybrid_probability from inclusion_dependencies i join unique_attributecombinations u on u.id = i.UAC_id order by i.id;" > "$SMALL/er25/inds.tsv"
  q "select id, storage_name, db_id, parent_id from datastorage order by id;" > "$SMALL/er25/datastorage.tsv"
  q "select u.server_type, u.datastorage_name, u.attribute_names, u.pk_score_hopf from unique_attributecombinations u order by u.id;" > "$SMALL/er25/uacs.tsv"
  echo "    ER'25 inclusions: $(( $(wc -l < "$SMALL/er25/inds.tsv") - 1 ))"
done

echo
echo "ALL DONE"
