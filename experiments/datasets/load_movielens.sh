#!/usr/bin/env bash
# Loads the MovieLens + TMDB + Wikidata instance into the running containers.
#
#   ./experiments/datasets/load_movielens.sh [dataDir]
#
# MovieLens's relational CSVs to PostgreSQL, TMDB's newline-delimited export to MongoDB, the
# Wikidata entity slice to Neo4j, following each source's published format.
#
# The stores are shared with the other corpora, so everything here is namespaced: schema
# `movielens` in PostgreSQL, database `tmdb` in MongoDB, and the `WikidataFilm` label in Neo4j.
# Rerunning drops and rewrites only those.
set -euo pipefail

# Stop Git Bash from rewriting container paths such as /tmp/x.json into Windows paths.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

source "$(dirname "$0")/../../config/env.sh"
DATA="${1:-$WORK_ROOT/movielens}"

# Container names and the PostgreSQL database are parameters so the loader can also fill the
# isolated single-corpus stores used for the ER'25 comparison, which reads one corpus per database
# URI. That MongoDB runs unauthenticated, because ER'25 derives authSource from the URI's database.
PG_CONTAINER="${PG_CONTAINER:-spind-postgres}"
PG_DATABASE="${PG_DATABASE:-mixed}"
MONGO_CONTAINER="${MONGO_CONTAINER:-spind-mongo}"
NEO4J_CONTAINER="${NEO4J_CONTAINER:-spind-neo4j}"
# "-" rather than ":-": an explicitly empty MONGO_AUTH means "connect without credentials".
MONGO_AUTH="${MONGO_AUTH---username spind --password spindpass --authenticationDatabase admin}"

echo "=== PostgreSQL: movielens.movies, links, tags, ratings ==="
for f in movies links tags ratings; do
  docker cp "$DATA/pg_$f.csv" "$PG_CONTAINER:/tmp/ml_$f.csv"
done

docker exec -i "$PG_CONTAINER" psql -U spind -d "$PG_DATABASE" <<'SQL'
DROP SCHEMA IF EXISTS movielens CASCADE;
CREATE SCHEMA movielens;

-- Every column is TEXT: MovieLens writes IMDb identifiers zero-padded without the "tt" prefix
-- Wikidata keeps, and a numeric column would normalise that difference away.
CREATE TABLE movielens.movies  (movieid TEXT, title TEXT, genres TEXT);
CREATE TABLE movielens.links   (movieid TEXT, imdbid TEXT, tmdbid TEXT);
CREATE TABLE movielens.tags    (userid TEXT, movieid TEXT, tag TEXT, timestamp TEXT);
CREATE TABLE movielens.ratings (userid TEXT, movieid TEXT, rating TEXT, timestamp TEXT);

\copy movielens.movies  FROM '/tmp/ml_movies.csv'  WITH (FORMAT csv, HEADER true)
\copy movielens.links   FROM '/tmp/ml_links.csv'   WITH (FORMAT csv, HEADER true)
\copy movielens.tags    FROM '/tmp/ml_tags.csv'    WITH (FORMAT csv, HEADER true)
\copy movielens.ratings FROM '/tmp/ml_ratings.csv' WITH (FORMAT csv, HEADER true)

SELECT 'movies' AS t, count(*) FROM movielens.movies
UNION ALL SELECT 'links', count(*) FROM movielens.links
UNION ALL SELECT 'tags', count(*) FROM movielens.tags
UNION ALL SELECT 'ratings', count(*) FROM movielens.ratings;
SQL

echo
echo "=== MongoDB: tmdb.movies (the publisher's daily identifier export) ==="
docker cp "$DATA/mongo_tmdb_movies.json" $MONGO_CONTAINER:/tmp/tmdb_movies.json
docker exec "$MONGO_CONTAINER" mongoimport $MONGO_AUTH \
    --db tmdb --collection movies --drop --file /tmp/tmdb_movies.json
docker exec "$MONGO_CONTAINER" mongosh --quiet $MONGO_AUTH \
    --eval 'print("tmdb.movies: " + db.getSiblingDB("tmdb").movies.countDocuments({}))'

echo
echo "=== Neo4j: WikidataFilm nodes ==="
docker cp "$DATA/neo4j_wikidata_film.csv" $NEO4J_CONTAINER:/var/lib/neo4j/import/wikidata_film.csv

# The two MovieLens identifier fields become list properties (one Wikidata item can carry
# several). The split is guarded because an empty field would yield [''].
docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword <<'CYPHER'
MATCH (n:WikidataFilm) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS;
CYPHER

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword <<'CYPHER'
LOAD CSV WITH HEADERS FROM 'file:///wikidata_film.csv' AS row
CALL (row) {
  CREATE (n:WikidataFilm)
  SET n.wikidata_id = row.wikidata_id,
      n.imdb_id = row.imdb_id,
      n.wikidata_tmdb_id = row.wikidata_tmdb_id,
      n.movielens_movie_ids =
        CASE WHEN row.movielens_movie_ids IS NULL OR row.movielens_movie_ids = ''
             THEN [] ELSE split(row.movielens_movie_ids, ';') END,
      n.movielens_tmdb_ids =
        CASE WHEN row.movielens_tmdb_ids IS NULL OR row.movielens_tmdb_ids = ''
             THEN [] ELSE split(row.movielens_tmdb_ids, ';') END
} IN TRANSACTIONS OF 5000 ROWS;
CYPHER

docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p testpassword \
  "MATCH (n:WikidataFilm) RETURN count(n) AS wikidata_film_nodes;"
