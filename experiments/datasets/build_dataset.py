"""Derives a three-model dataset from a single TPC-H source.

All three representations come from the same relational tables, so every cross-model inclusion
dependency is known by construction rather than by inspection:

    Postgres (relational) : customer, nation, region
    MongoDB  (document)   : orders, each embedding its lineitems as an array
    Neo4j    (graph)      : Part and Supplier nodes

A controlled fraction of customers is then deleted from Postgres. That turns the
document->relational foreign key into a *partial* IND with a coverage value we compute here, so
discovery can be checked against a known rho rather than merely "found something".

Outputs to OUT_DIR:
    pg_customer.csv, pg_nation.csv, pg_region.csv
    mongo_orders.json          (one JSON document per line, for mongoimport)
    neo4j_part.csv, neo4j_supplier.csv
    ground_truth.json
"""
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

def _flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


SOURCE = paths.DATA_ROOT / _flag("source", "TPC-H 1")
OUT_DIR = Path(_flag("out", str(paths.DATASET)))
# Number of orders drawn from the source. This is the size knob for the scalability curve: the
# document store grows with it directly and the graph store follows, since parts and suppliers are
# restricted to those actually referenced.
ORDER_LIMIT = int(_flag("orders", "40000"))
# Fraction of referenced customers removed from the relational store. Chosen small enough that
# the dependency is still obviously meaningful, large enough to be well clear of measurement
# noise in the reported coverage.
DROP_FRACTION = 0.03
SEED = 20260817

COLUMNS = {
    "customer": ["c_custkey", "c_name", "c_address", "c_nationkey", "c_phone",
                 "c_acctbal", "c_mktsegment", "c_comment"],
    "orders": ["o_orderkey", "o_custkey", "o_orderstatus", "o_totalprice", "o_orderdate",
               "o_orderpriority", "o_clerk", "o_shippriority", "o_comment"],
    "lineitem": ["l_orderkey", "l_partkey", "l_suppkey", "l_linenumber", "l_quantity",
                 "l_extendedprice", "l_discount", "l_tax", "l_returnflag", "l_linestatus",
                 "l_shipdate", "l_commitdate", "l_receiptdate", "l_shipinstruct",
                 "l_shipmode", "l_comment"],
    "part": ["p_partkey", "p_name", "p_mfgr", "p_brand", "p_type", "p_size",
             "p_container", "p_retailprice", "p_comment"],
    "supplier": ["s_suppkey", "s_name", "s_address", "s_nationkey", "s_phone",
                 "s_acctbal", "s_comment"],
    "partsupp": ["ps_partkey", "ps_suppkey", "ps_availqty", "ps_supplycost", "ps_comment"],
    "nation": ["n_nationkey", "n_name", "n_regionkey", "n_comment"],
    "region": ["r_regionkey", "r_name", "r_comment"],
}


# The fields the order documents embed per line item, and the node properties the graph store
# actually carries (load_stores.sh omits the comment columns). The relational baseline mirrors
# both exactly, so the two settings differ in *layout* and not in which values are present.
EMBEDDED_LINEITEM_FIELDS = ("l_partkey", "l_suppkey", "l_linenumber",
                            "l_quantity", "l_returnflag", "l_shipmode")
GRAPH_PART_FIELDS = ("p_partkey", "p_name", "p_mfgr", "p_brand", "p_type", "p_size",
                     "p_container", "p_retailprice")
GRAPH_SUPPLIER_FIELDS = ("s_suppkey", "s_name", "s_address", "s_nationkey",
                         "s_phone", "s_acctbal")
# partsupp is a relationship with attributes: a table in the relational layout, a SUPPLIES edge
# in the graph, so the layout comparison covers edges as well as arrays.
GRAPH_PARTSUPP_FIELDS = ("ps_partkey", "ps_suppkey", "ps_availqty", "ps_supplycost")


def read_tbl(name, limit=None):
    """TPC-H .tbl files are pipe-separated with a trailing delimiter and no header."""
    columns = COLUMNS[name]
    rows = []
    with (SOURCE / f"{name}.tbl").open("r", encoding="utf-8", errors="replace") as handle:
        for i, line in enumerate(handle):
            if limit is not None and i >= limit:
                break
            fields = line.rstrip("\n").rstrip("|").split("|")
            rows.append(dict(zip(columns, fields)))
    return rows


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[c] for c in columns])


def write_relational_baseline(orders, lineitems_by_order, customers, nations, regions,
                              parts, suppliers, partsupps):
    """Writes the same data as seven flat relations, plus a catalog the runners can read.

    This is the control for the multi-model setting: identical values and attribute sets, laid out
    as a single relational store would hold them. The one asymmetry is l_orderkey, which
    normalization needs as a foreign key while nesting expresses the relationship structurally.
    """
    out = OUT_DIR / "rel"
    out.mkdir(parents=True, exist_ok=True)

    order_columns = COLUMNS["orders"]
    lineitem_columns = ["l_orderkey", *EMBEDDED_LINEITEM_FIELDS]
    flat_lineitems = [li for order in orders
                      for li in lineitems_by_order.get(order["o_orderkey"], [])]

    relations = [
        ("customer", COLUMNS["customer"], customers),
        ("nation", COLUMNS["nation"], nations),
        ("region", COLUMNS["region"], regions),
        ("orders", order_columns, orders),
        ("lineitem", lineitem_columns, flat_lineitems),
        ("part", list(GRAPH_PART_FIELDS), parts),
        ("supplier", list(GRAPH_SUPPLIER_FIELDS), suppliers),
        # The relational counterpart of the SUPPLIES edge: the same relationship,
        # expressed as a table because a relation cannot carry attributes on a link.
        ("partsupp", list(GRAPH_PARTSUPP_FIELDS), partsupps),
    ]

    catalog = []
    for name, columns, rows in relations:
        write_csv(out / f"{name}.csv", columns, rows)
        catalog.append({"relation": f"rel.{name}", "scope": "base", "rows": len(rows),
                        "columns": len(columns), "file": f"{name}.csv"})
    # One entry per line, matching what the Java exporters write, so both catalogs diff
    # the same way and any reader of one reads the other.
    body = ",\n".join("  " + json.dumps(entry) for entry in catalog)
    (out / "catalog.json").write_text("[\n" + body + "\n]\n", encoding="utf-8")

    total = sum(entry["columns"] for entry in catalog)
    print(f"relational baseline  {len(catalog)} relations, {total} attributes -> {out}")


def main():
    random.seed(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    orders = read_tbl("orders", ORDER_LIMIT)
    order_keys = {o["o_orderkey"] for o in orders}
    max_order_key = max(int(k) for k in order_keys)

    # lineitem is sorted by orderkey, so reading until past the largest selected key is enough.
    lineitems_by_order = {}
    with (SOURCE / "lineitem.tbl").open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            fields = line.rstrip("\n").rstrip("|").split("|")
            row = dict(zip(COLUMNS["lineitem"], fields))
            if int(row["l_orderkey"]) > max_order_key:
                break
            if row["l_orderkey"] in order_keys:
                lineitems_by_order.setdefault(row["l_orderkey"], []).append(row)

    referenced_parts = {li["l_partkey"] for lis in lineitems_by_order.values() for li in lis}
    referenced_supps = {li["l_suppkey"] for lis in lineitems_by_order.values() for li in lis}
    referenced_custs = {o["o_custkey"] for o in orders}

    customers = [c for c in read_tbl("customer") if c["c_custkey"] in referenced_custs]
    parts = [p for p in read_tbl("part") if p["p_partkey"] in referenced_parts]
    suppliers = [s for s in read_tbl("supplier") if s["s_suppkey"] in referenced_supps]
    nations = read_tbl("nation")
    regions = read_tbl("region")
    part_keys = {p["p_partkey"] for p in parts}
    supplier_keys = {s["s_suppkey"] for s in suppliers}
    partsupps = [ps for ps in read_tbl("partsupp")
                 if ps["ps_partkey"] in part_keys and ps["ps_suppkey"] in supplier_keys]

    # --- inject known partiality -------------------------------------------------------------
    # Removing customers from the relational side leaves order documents referencing keys that no
    # longer exist. Coverage is computed over occurrences, matching SPIND's duplicate-aware
    # semantics: each order counts once, so dropping a customer costs as many violations as that
    # customer has orders.
    droppable = sorted(referenced_custs)
    random.shuffle(droppable)
    dropped = set(droppable[:int(len(droppable) * DROP_FRACTION)])
    customers = [c for c in customers if c["c_custkey"] not in dropped]

    # Coverage of the document->relational key depends on the extractor's scope:
    #   document scope   - one observation per order
    #   array-item scope - one observation per embedded lineitem (exploding the array repeats
    #                      every parent field once per element)
    # The adapter explodes the array, so the array-item figure is the expected coverage.
    lineitem_counts = {k: max(1, len(v)) for k, v in lineitems_by_order.items()}
    covered_orders = sum(1 for o in orders if o["o_custkey"] not in dropped)
    coverage_document_scope = covered_orders / len(orders)

    observations = sum(lineitem_counts.get(o["o_orderkey"], 1) for o in orders)
    covered_observations = sum(lineitem_counts.get(o["o_orderkey"], 1)
                               for o in orders if o["o_custkey"] not in dropped)
    custkey_coverage = covered_observations / observations

    # --- write the three representations ------------------------------------------------------
    write_csv(OUT_DIR / "pg_customer.csv", COLUMNS["customer"], customers)
    write_csv(OUT_DIR / "pg_nation.csv", COLUMNS["nation"], nations)
    write_csv(OUT_DIR / "pg_region.csv", COLUMNS["region"], regions)
    write_csv(OUT_DIR / "neo4j_part.csv", COLUMNS["part"], parts)
    write_csv(OUT_DIR / "neo4j_supplier.csv", COLUMNS["supplier"], suppliers)
    write_csv(OUT_DIR / "neo4j_supplies.csv", list(GRAPH_PARTSUPP_FIELDS), partsupps)

    with (OUT_DIR / "mongo_orders.json").open("w", encoding="utf-8") as handle:
        for order in orders:
            # The order key becomes the document identity. A minted ObjectId would add an
            # attribute with no source counterpart, contributing a spurious UCC and a trivial
            # determinant to every FD.
            document = {"_id": order["o_orderkey"]}
            document.update({k: v for k, v in order.items() if k != "o_orderkey"})
            document["lineitems"] = [
                {k: li[k] for k in EMBEDDED_LINEITEM_FIELDS}
                for li in lineitems_by_order.get(order["o_orderkey"], [])
            ]
            handle.write(json.dumps(document) + "\n")

    write_relational_baseline(orders, lineitems_by_order, customers, nations, regions,
                              parts, suppliers, partsupps)

    ground_truth = {
        "orders": len(orders),
        "lineitems": sum(len(v) for v in lineitems_by_order.values()),
        "customers_kept": len(customers),
        "customers_dropped": len(dropped),
        "parts": len(parts),
        "suppliers": len(suppliers),
        "expected_inds": [
            {"dependent": "shop.orders.o_custkey", "referenced": "public.customer.c_custkey",
             "models": "document -> relational", "expected_coverage": round(custkey_coverage, 6),
             "coverage_under_document_scope": round(coverage_document_scope, 6),
             "note": "partiality injected by deleting customers; coverage is scope-dependent"},
            {"dependent": "shop.orders@lineitems.lineitems.l_partkey", "referenced": "neo4j:Part.p_partkey",
             "models": "document -> graph", "expected_coverage": 1.0},
            {"dependent": "shop.orders@lineitems.lineitems.l_suppkey", "referenced": "neo4j:Supplier.s_suppkey",
             "models": "document -> graph", "expected_coverage": 1.0},
            {"dependent": "neo4j:Supplier.s_nationkey", "referenced": "public.nation.n_nationkey",
             "models": "graph -> relational", "expected_coverage": 1.0},
            {"dependent": "public.customer.c_nationkey", "referenced": "public.nation.n_nationkey",
             "models": "relational -> relational", "expected_coverage": 1.0},
            {"dependent": "public.nation.n_regionkey", "referenced": "public.region.r_regionkey",
             "models": "relational -> relational", "expected_coverage": 1.0},
            # partsupp is loaded as the SUPPLIES edge, so its two declared foreign keys surface
            # as projections of the edge's endpoints rather than under their relational names.
            {"dependent": "neo4j:-[SUPPLIES]->.target.p_partkey", "referenced": "neo4j:Part.p_partkey",
             "models": "graph -> graph", "expected_coverage": 1.0,
             "note": "TPC-H partsupp.ps_partkey -> part.p_partkey, as an edge endpoint"},
            {"dependent": "neo4j:-[SUPPLIES]->.source.s_suppkey", "referenced": "neo4j:Supplier.s_suppkey",
             "models": "graph -> graph", "expected_coverage": 1.0,
             "note": "TPC-H partsupp.ps_suppkey -> supplier.s_suppkey, as an edge endpoint"},
        ],
    }
    (OUT_DIR / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2))

    print(f"orders            {len(orders):,}")
    print(f"lineitems         {ground_truth['lineitems']:,}")
    print(f"customers kept    {len(customers):,} (dropped {len(dropped):,})")
    print(f"parts             {len(parts):,}")
    print(f"suppliers         {len(suppliers):,}")
    print(f"supplies edges    {len(partsupps):,}")
    print(f"expected coverage o_custkey -> c_custkey = {custkey_coverage:.6f} (array-item scope)")
    print(f"                                        = {coverage_document_scope:.6f} (document scope)")
    print(f"written to {OUT_DIR}")


if __name__ == "__main__":
    main()
