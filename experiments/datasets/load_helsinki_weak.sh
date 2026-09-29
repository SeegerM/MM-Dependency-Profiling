#!/usr/bin/env bash
# Loads the Helsinki UDBMS Film and University datasets into the running containers.
#
#   ./experiments/datasets/load_helsinki_weak.sh [film|university|both] [dataRoot]
#
# Film is a document dump against two independently built graphs; University is a relational
# table against a location graph. Neither publisher ships a third store.
#
# Namespaced so the stores can hold every corpus at once: database `hfilm` in MongoDB, schema
# `huniversity` in PostgreSQL, and the `FilmDBpedia`, `FilmYago` and `ULocation` node labels with
# the `DBPEDIA`, `YAGO` relationship types in Neo4j.
set -euo pipefail

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

source "$(dirname "$0")/../../config/env.sh"
WHICH="${1:-both}"
ROOT="${2:-$WORK_ROOT}"

PG_CONTAINER="${PG_CONTAINER:-spind-postgres}"
PG_DATABASE="${PG_DATABASE:-mixed}"
MONGO_CONTAINER="${MONGO_CONTAINER:-spind-mongo}"
NEO4J_CONTAINER="${NEO4J_CONTAINER:-spind-neo4j}"
MONGO_AUTH="${MONGO_AUTH---username spind --password spindpass --authenticationDatabase admin}"

cypher() { docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword "$@" 2>&1 | grep -vE "^WARNING|jansi|native-access|^$" || true; }

load_graph() {  # label nodesFile edgesFile relType
  local label="$1" nodes="$2" edges="$3" rel="$4"
  docker cp "$nodes" "$NEO4J_CONTAINER:/var/lib/neo4j/import/$(basename "$nodes")"
  docker cp "$edges" "$NEO4J_CONTAINER:/var/lib/neo4j/import/$(basename "$edges")"
  cypher "MATCH (n:$label) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 20000 ROWS;"
  cypher "CREATE INDEX ${label}_id IF NOT EXISTS FOR (n:$label) ON (n.id);"
  cypher "LOAD CSV WITH HEADERS FROM 'file:///$(basename "$nodes")' AS row
          CALL (row) { CREATE (:$label {id: row.id}) } IN TRANSACTIONS OF 20000 ROWS;"
  cypher "CALL db.awaitIndexes(600);"
  cypher "LOAD CSV WITH HEADERS FROM 'file:///$(basename "$edges")' AS row
          CALL (row) {
            MATCH (s:$label {id: row.subject})
            MATCH (o:$label {id: row.object})
            CREATE (s)-[:$rel {predicate: row.predicate}]->(o)
          } IN TRANSACTIONS OF 20000 ROWS;"
  cypher "MATCH (n:$label) RETURN count(n) AS nodes;"
}

if [ "$WHICH" = "film" ] || [ "$WHICH" = "both" ]; then
  DATA="$ROOT/hfilm"
  echo "=== MongoDB: hfilm.films ==="
  docker cp "$DATA/films.jsonl" "$MONGO_CONTAINER:/tmp/hfilm_films.jsonl"
  docker exec -i "$MONGO_CONTAINER" mongosh $MONGO_AUTH --quiet --eval \
    'db.getSiblingDB("hfilm").films.drop()'
  docker exec -i "$MONGO_CONTAINER" mongoimport $MONGO_AUTH \
    --db hfilm --collection films --file /tmp/hfilm_films.jsonl
  docker exec -i "$MONGO_CONTAINER" mongosh $MONGO_AUTH --quiet --eval \
    'print("films: " + db.getSiblingDB("hfilm").films.countDocuments({}))'

  echo
  echo "=== Neo4j: FilmDBpedia and FilmYago ==="
  load_graph FilmDBpedia "$DATA/film_dbpedia_nodes.csv" "$DATA/film_dbpedia_edges.csv" DBPEDIA
  load_graph FilmYago    "$DATA/film_yago_nodes.csv"    "$DATA/film_yago_edges.csv"    YAGO
fi

if [ "$WHICH" = "university" ] || [ "$WHICH" = "both" ]; then
  DATA="$ROOT/huniversity"
  echo
  echo "=== PostgreSQL: huniversity.scorecard ==="
  # PostgreSQL caps a table at 1600 columns and the scorecard has 1,729, so only the identifying
  # and locating columns are loaded; the rest are outcome statistics.
  docker cp "$DATA/scorecard_subset.csv" "$PG_CONTAINER:/tmp/huniversity_scorecard.csv"
  docker exec -i "$PG_CONTAINER" psql -U spind -d "$PG_DATABASE" <<'SQL'
DROP SCHEMA IF EXISTS huniversity CASCADE;
CREATE SCHEMA huniversity;
CREATE TABLE huniversity.scorecard (
    unitid TEXT, opeid TEXT, opeid6 TEXT, instnm TEXT, city TEXT, stabbr TEXT,
    zip TEXT, insturl TEXT, latitude TEXT, longitude TEXT);
\copy huniversity.scorecard FROM '/tmp/huniversity_scorecard.csv' WITH (FORMAT csv, HEADER true)
SELECT 'scorecard' AS t, count(*) FROM huniversity.scorecard;
SQL

  echo
  echo "=== Neo4j: ULocation nodes ==="
  docker cp "$DATA/location_nodes.csv" "$NEO4J_CONTAINER:/var/lib/neo4j/import/ulocation_nodes.csv"
  cypher "MATCH (n:ULocation) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 20000 ROWS;"
  # Every object in the location graph is a literal, so its coordinates are node properties.
  cypher "LOAD CSV WITH HEADERS FROM 'file:///ulocation_nodes.csv' AS row
          CALL (row) {
            CREATE (:ULocation {id: row.id, latitude: row.latitude, longitude: row.longitude})
          } IN TRANSACTIONS OF 20000 ROWS;"
  cypher "MATCH (n:ULocation) RETURN count(n) AS nodes;"
fi
