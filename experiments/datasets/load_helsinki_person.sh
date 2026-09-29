#!/usr/bin/env bash
# Loads the Helsinki UDBMS Person dataset into the running containers.
#
#   ./experiments/datasets/load_helsinki_person.sh [dataDir]
#
# Documents to MongoDB and the triples to Neo4j, the split the publisher ships; the dataset has no
# relational side. The graph is loaded as untyped nodes with one MENTIONS edge per triple, the
# predicate kept as an edge property.
#
# Namespaced so the stores can hold every corpus at once: database `hperson` in MongoDB, the
# `HPerson` node label and the `MENTIONS` relationship type in Neo4j.
set -euo pipefail

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

source "$(dirname "$0")/../../config/env.sh"
DATA="${1:-$WORK_ROOT/hperson}"

PG_CONTAINER="${PG_CONTAINER:-spind-postgres}"
MONGO_CONTAINER="${MONGO_CONTAINER:-spind-mongo}"
NEO4J_CONTAINER="${NEO4J_CONTAINER:-spind-neo4j}"
MONGO_AUTH="${MONGO_AUTH---username spind --password spindpass --authenticationDatabase admin}"

echo "=== MongoDB: hperson.pages ==="
docker cp "$DATA/person.jsonl" "$MONGO_CONTAINER:/tmp/hperson_pages.jsonl"
docker exec -i "$MONGO_CONTAINER" mongosh $MONGO_AUTH --quiet --eval \
  'db.getSiblingDB("hperson").pages.drop()'
docker exec -i "$MONGO_CONTAINER" mongoimport $MONGO_AUTH \
  --db hperson --collection pages --file /tmp/hperson_pages.jsonl
docker exec -i "$MONGO_CONTAINER" mongosh $MONGO_AUTH --quiet --eval \
  'print("pages: " + db.getSiblingDB("hperson").pages.countDocuments({}))'

echo
echo "=== Neo4j: HPerson subject nodes and MENTIONS edges ==="
docker cp "$DATA/person_nodes.csv"   "$NEO4J_CONTAINER:/var/lib/neo4j/import/hperson_nodes.csv"
docker cp "$DATA/person_triples.csv" "$NEO4J_CONTAINER:/var/lib/neo4j/import/hperson_triples.csv"

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword <<'CYPHER'
MATCH (n:HPerson) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 20000 ROWS;
CYPHER

# Without the index each edge costs two label scans over half a million nodes.
docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword \
  "CREATE INDEX hperson_id IF NOT EXISTS FOR (n:HPerson) ON (n.id);"

# The node file holds every subject and object in the full triple file, even when the edges are
# sampled, so the document-to-graph correspondence does not depend on the sample.
docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword <<'CYPHER'
LOAD CSV WITH HEADERS FROM 'file:///hperson_nodes.csv' AS row
CALL (row) { CREATE (:HPerson {id: row.id}) } IN TRANSACTIONS OF 20000 ROWS;
CYPHER

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword \
  "CALL db.awaitIndexes(600);"

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword <<'CYPHER'
LOAD CSV WITH HEADERS FROM 'file:///hperson_triples.csv' AS row
CALL (row) {
  MATCH (s:HPerson {id: row.subject})
  MATCH (o:HPerson {id: row.object})
  CREATE (s)-[:MENTIONS {predicate: row.predicate}]->(o)
} IN TRANSACTIONS OF 20000 ROWS;
CYPHER

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword \
  "MATCH (n:HPerson) RETURN count(n) AS nodes;"
docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword \
  "MATCH ()-[r:MENTIONS]->() RETURN count(r) AS edges;"
