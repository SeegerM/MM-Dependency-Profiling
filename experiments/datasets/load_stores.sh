#!/usr/bin/env bash
# Loads the generated three-model dataset into the running containers.
#
# The containers start empty (docker-compose.yml); every table, collection and label read by the
# discovery run is created here.
set -euo pipefail

# Stop Git Bash from rewriting container paths such as /tmp/x.json into Windows paths.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

source "$(dirname "$0")/../../config/env.sh"
# The dataset directory is an argument so the scalability runner can load each size in turn.
DATA="${1:-$WORK_ROOT/mm_tpch}"

echo "=== PostgreSQL: customer, nation, region ==="
docker cp "$DATA/pg_customer.csv" spind-postgres:/tmp/pg_customer.csv
docker cp "$DATA/pg_nation.csv"   spind-postgres:/tmp/pg_nation.csv
docker cp "$DATA/pg_region.csv"   spind-postgres:/tmp/pg_region.csv

docker exec -i spind-postgres psql -U spind -d mixed <<'SQL'
DROP TABLE IF EXISTS public.customer, public.nation, public.region;

CREATE TABLE public.customer (
    c_custkey TEXT, c_name TEXT, c_address TEXT, c_nationkey TEXT,
    c_phone TEXT, c_acctbal TEXT, c_mktsegment TEXT, c_comment TEXT);
CREATE TABLE public.nation (
    n_nationkey TEXT, n_name TEXT, n_regionkey TEXT, n_comment TEXT);
CREATE TABLE public.region (
    r_regionkey TEXT, r_name TEXT, r_comment TEXT);

\copy public.customer FROM '/tmp/pg_customer.csv' WITH (FORMAT csv, HEADER true)
\copy public.nation   FROM '/tmp/pg_nation.csv'   WITH (FORMAT csv, HEADER true)
\copy public.region   FROM '/tmp/pg_region.csv'   WITH (FORMAT csv, HEADER true)

SELECT 'customer' AS t, count(*) FROM public.customer
UNION ALL SELECT 'nation', count(*) FROM public.nation
UNION ALL SELECT 'region', count(*) FROM public.region;
SQL

echo
echo "=== MongoDB: shop.orders (documents with embedded lineitems) ==="
docker cp "$DATA/mongo_orders.json" spind-mongo:/tmp/mongo_orders.json
docker exec spind-mongo mongoimport \
    --username spind --password spindpass --authenticationDatabase admin \
    --db shop --collection orders --drop --file /tmp/mongo_orders.json
docker exec spind-mongo mongosh --quiet \
    --username spind --password spindpass --authenticationDatabase admin \
    --eval 'print("orders: " + db.getSiblingDB("shop").orders.countDocuments({}))'

echo
echo "=== Neo4j: Part and Supplier nodes ==="
docker cp "$DATA/neo4j_part.csv"     spind-neo4j:/var/lib/neo4j/import/part.csv
docker cp "$DATA/neo4j_supplier.csv" spind-neo4j:/var/lib/neo4j/import/supplier.csv

docker exec -i spind-neo4j cypher-shell -u neo4j -p testpassword <<'CYPHER'
MATCH (n:Part) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS;
MATCH (n:Supplier) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS;
CYPHER

docker exec -i spind-neo4j cypher-shell -u neo4j -p testpassword <<'CYPHER'
LOAD CSV WITH HEADERS FROM 'file:///part.csv' AS row
CALL (row) {
  CREATE (:Part {p_partkey: row.p_partkey, p_name: row.p_name, p_mfgr: row.p_mfgr,
                 p_brand: row.p_brand, p_type: row.p_type, p_size: row.p_size,
                 p_container: row.p_container, p_retailprice: row.p_retailprice})
} IN TRANSACTIONS OF 10000 ROWS;
CYPHER

docker exec -i spind-neo4j cypher-shell -u neo4j -p testpassword <<'CYPHER'
LOAD CSV WITH HEADERS FROM 'file:///supplier.csv' AS row
CALL (row) {
  CREATE (:Supplier {s_suppkey: row.s_suppkey, s_name: row.s_name, s_address: row.s_address,
                     s_nationkey: row.s_nationkey, s_phone: row.s_phone, s_acctbal: row.s_acctbal})
} IN TRANSACTIONS OF 10000 ROWS;
CYPHER

docker cp "$DATA/neo4j_supplies.csv" spind-neo4j:/var/lib/neo4j/import/supplies.csv

# partsupp as a SUPPLIES edge carrying its own attributes, matched on the node keys.
docker exec -i spind-neo4j cypher-shell -u neo4j -p testpassword <<'CYPHER'
// Constraints rather than plain indexes: the meta-level export projects edge endpoints through
// the properties a label declares unique, and a constraint also creates a backing index.
CREATE CONSTRAINT part_key IF NOT EXISTS FOR (p:Part) REQUIRE p.p_partkey IS UNIQUE;
CREATE CONSTRAINT supplier_key IF NOT EXISTS FOR (s:Supplier) REQUIRE s.s_suppkey IS UNIQUE;
CYPHER

docker exec -i spind-neo4j cypher-shell -u neo4j -p testpassword <<'CYPHER'
LOAD CSV WITH HEADERS FROM 'file:///supplies.csv' AS row
CALL (row) {
  MATCH (s:Supplier {s_suppkey: row.ps_suppkey}), (p:Part {p_partkey: row.ps_partkey})
  CREATE (s)-[:SUPPLIES {ps_availqty: row.ps_availqty, ps_supplycost: row.ps_supplycost}]->(p)
} IN TRANSACTIONS OF 10000 ROWS;
CYPHER

docker exec -i spind-neo4j cypher-shell -u neo4j -p testpassword \
    "MATCH (p:Part) RETURN 'Part' AS label, count(p) AS n;"
docker exec -i spind-neo4j cypher-shell -u neo4j -p testpassword \
    "MATCH ()-[r:SUPPLIES]->() RETURN count(r) AS supplies_edges;"

echo
echo "load complete"
