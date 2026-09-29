#!/usr/bin/env bash
# Loads the Helsinki UDBMS Patent dataset into the running containers.
#
#   ./experiments/datasets/load_helsinki_patent.sh [dataDir]
#
# Relational tables to PostgreSQL and the citation edge list to Neo4j, the split the publisher
# ships. The dataset has no document side.
#
# Namespaced so the stores can hold every corpus at once: schema `hpatent` in PostgreSQL, the
# `HPatent` node label and the `CITES` relationship type in Neo4j.
set -euo pipefail

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

source "$(dirname "$0")/../../config/env.sh"
DATA="${1:-$WORK_ROOT/hpatent}"

# Container names and the PostgreSQL database are parameters so the loader can also fill the
# isolated single-corpus stores used for the ER'25 comparison, which reads one corpus per database
# URI. That MongoDB runs unauthenticated, because ER'25 derives authSource from the URI's database.
PG_CONTAINER="${PG_CONTAINER:-spind-postgres}"
PG_DATABASE="${PG_DATABASE:-mixed}"
MONGO_CONTAINER="${MONGO_CONTAINER:-spind-mongo}"
NEO4J_CONTAINER="${NEO4J_CONTAINER:-spind-neo4j}"
# "-" rather than ":-": an explicitly empty MONGO_AUTH means "connect without credentials".
MONGO_AUTH="${MONGO_AUTH---username spind --password spindpass --authenticationDatabase admin}"

echo "=== PostgreSQL: hpatent.patent, inventor, assignee, class, category ==="
for f in patent inventor assignee class category; do
  docker cp "$DATA/pg_$f.csv" "$PG_CONTAINER:/tmp/hp_$f.csv"
done

# TEXT throughout: a typed column would normalise away representation differences in the source.
docker exec -i "$PG_CONTAINER" psql -U spind -d "$PG_DATABASE" <<'SQL'
DROP SCHEMA IF EXISTS hpatent CASCADE;
CREATE SCHEMA hpatent;

CREATE TABLE hpatent.patent (
    patent TEXT, gyear TEXT, gdate TEXT, appyear TEXT, country TEXT, postate TEXT,
    assignee TEXT, asscode TEXT, claims TEXT, nclass TEXT, cat TEXT, subcat TEXT,
    cmade TEXT, creceive TEXT, ratiocit TEXT, general TEXT, original TEXT,
    fwdaplag TEXT, bckgtlag TEXT, selfctub TEXT, selfctlb TEXT, secdupbd TEXT, secdlwbd TEXT);
CREATE TABLE hpatent.inventor (
    patent TEXT, lastnam TEXT, firstnam TEXT, midnam TEXT, modifnam TEXT, street TEXT,
    city TEXT, postate TEXT, country TEXT, zip TEXT, invseq TEXT);
CREATE TABLE hpatent.assignee (
    assignee TEXT, assname TEXT, cname TEXT, cusip TEXT, own TEXT, pname TEXT, sname TEXT);
CREATE TABLE hpatent.class (class TEXT, cname TEXT, subcat TEXT, cat TEXT);
CREATE TABLE hpatent.category (cat TEXT, subcat TEXT, subcatname TEXT, catnameshort TEXT, catenamelong TEXT);

\copy hpatent.patent   FROM '/tmp/hp_patent.csv'   WITH (FORMAT csv, HEADER true)
\copy hpatent.inventor FROM '/tmp/hp_inventor.csv' WITH (FORMAT csv, HEADER true)
\copy hpatent.assignee FROM '/tmp/hp_assignee.csv' WITH (FORMAT csv, HEADER true)
\copy hpatent.class    FROM '/tmp/hp_class.csv'    WITH (FORMAT csv, HEADER true)
\copy hpatent.category FROM '/tmp/hp_category.csv' WITH (FORMAT csv, HEADER true)

SELECT 'patent' AS t, count(*) FROM hpatent.patent
UNION ALL SELECT 'inventor', count(*) FROM hpatent.inventor
UNION ALL SELECT 'assignee', count(*) FROM hpatent.assignee
UNION ALL SELECT 'class', count(*) FROM hpatent.class
UNION ALL SELECT 'category', count(*) FROM hpatent.category;
SQL

echo
echo "=== Neo4j: HPatent nodes and CITES edges ==="
docker cp "$DATA/neo4j_patent_nodes.csv" $NEO4J_CONTAINER:/var/lib/neo4j/import/hpatent_nodes.csv
docker cp "$DATA/neo4j_citations.csv"    $NEO4J_CONTAINER:/var/lib/neo4j/import/hpatent_citations.csv

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword <<'CYPHER'
MATCH (n:HPatent) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 20000 ROWS;
CYPHER

# Without the index each edge costs two label scans over more than a million nodes.
docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword \
  "CREATE INDEX hpatent_id IF NOT EXISTS FOR (n:HPatent) ON (n.id);"

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword <<'CYPHER'
LOAD CSV WITH HEADERS FROM 'file:///hpatent_nodes.csv' AS row
CALL (row) { CREATE (:HPatent {id: row.id}) } IN TRANSACTIONS OF 20000 ROWS;
CYPHER

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword \
  "CALL db.awaitIndexes(600);"

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword <<'CYPHER'
LOAD CSV WITH HEADERS FROM 'file:///hpatent_citations.csv' AS row
CALL (row) {
  MATCH (a:HPatent {id: row.citing}), (b:HPatent {id: row.cited})
  CREATE (a)-[:CITES]->(b)
} IN TRANSACTIONS OF 10000 ROWS;
CYPHER

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword \
  "MATCH (n:HPatent) RETURN count(n) AS hpatent_nodes;"
docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword \
  "MATCH ()-[r:CITES]->() RETURN count(r) AS cites_edges;"
