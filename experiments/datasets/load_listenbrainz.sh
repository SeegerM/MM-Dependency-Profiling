#!/usr/bin/env bash
# Loads the ListenBrainz instance into the running containers.
#
#   ./experiments/datasets/load_listenbrainz.sh [dataDir]
#
# Listen events to MongoDB, popularity tables to PostgreSQL, the artist cache to Neo4j, following
# the format each part is published in.
#
# Namespaced so every corpus can share the stores: schema `listenbrainz` in PostgreSQL, database
# `listenbrainz` in MongoDB, the `LBArtist` label in Neo4j.
set -euo pipefail

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

source "$(dirname "$0")/../../config/env.sh"
DATA="${1:-$WORK_ROOT/listenbrainz}"

# Container names and the PostgreSQL database are parameters so the loader can also fill the
# isolated single-corpus stores used for the ER'25 comparison, which reads one corpus per database
# URI. That MongoDB runs unauthenticated, because ER'25 derives authSource from the URI's database.
PG_CONTAINER="${PG_CONTAINER:-spind-postgres}"
PG_DATABASE="${PG_DATABASE:-mixed}"
MONGO_CONTAINER="${MONGO_CONTAINER:-spind-mongo}"
NEO4J_CONTAINER="${NEO4J_CONTAINER:-spind-neo4j}"
# "-" rather than ":-": an explicitly empty MONGO_AUTH means "connect without credentials".
MONGO_AUTH="${MONGO_AUTH---username spind --password spindpass --authenticationDatabase admin}"

echo "=== PostgreSQL: listenbrainz.top_recording, mlhd_top_recording ==="
docker cp "$DATA/pg_top_recording.csv"      $PG_CONTAINER:/tmp/lb_top_recording.csv
docker cp "$DATA/pg_mlhd_top_recording.csv" $PG_CONTAINER:/tmp/lb_mlhd_top_recording.csv

docker exec -i "$PG_CONTAINER" psql -U spind -d "$PG_DATABASE" <<'SQL'
DROP SCHEMA IF EXISTS listenbrainz CASCADE;
CREATE SCHEMA listenbrainz;

CREATE TABLE listenbrainz.top_recording (
    artist_mbid TEXT, recording_mbid TEXT, total_listen_count TEXT, total_user_count TEXT);
CREATE TABLE listenbrainz.mlhd_top_recording (
    artist_mbid TEXT, recording_mbid TEXT, total_listen_count TEXT, total_user_count TEXT);

\copy listenbrainz.top_recording      FROM '/tmp/lb_top_recording.csv'      WITH (FORMAT csv, HEADER true)
\copy listenbrainz.mlhd_top_recording FROM '/tmp/lb_mlhd_top_recording.csv' WITH (FORMAT csv, HEADER true)

SELECT 'top_recording' AS t, count(*) FROM listenbrainz.top_recording
UNION ALL SELECT 'mlhd_top_recording', count(*) FROM listenbrainz.mlhd_top_recording;
SQL

echo
echo "=== MongoDB: listenbrainz.listens (nested listen events) ==="
docker cp "$DATA/mongo_listens.json" $MONGO_CONTAINER:/tmp/lb_listens.json
docker exec "$MONGO_CONTAINER" mongoimport $MONGO_AUTH \
    --db listenbrainz --collection listens --drop --file /tmp/lb_listens.json
docker exec "$MONGO_CONTAINER" mongosh --quiet $MONGO_AUTH \
    --eval 'print("listenbrainz.listens: " + db.getSiblingDB("listenbrainz").listens.countDocuments({}))'

echo
echo "=== Neo4j: LBArtist nodes ==="
docker cp "$DATA/neo4j_artists.csv" $NEO4J_CONTAINER:/var/lib/neo4j/import/lb_artists.csv

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword <<'CYPHER'
MATCH (n:LBArtist) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS;
CYPHER

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword <<'CYPHER'
LOAD CSV WITH HEADERS FROM 'file:///lb_artists.csv' AS row
CALL (row) {
  CREATE (:LBArtist {artist_mbid: row.artist_mbid, name: row.name, area: row.area,
                     type: row.type, gender: row.gender, wikidata_url: row.wikidata_url})
} IN TRANSACTIONS OF 5000 ROWS;
CYPHER

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword \
  "MATCH (n:LBArtist) RETURN count(n) AS lbartist_nodes;"
