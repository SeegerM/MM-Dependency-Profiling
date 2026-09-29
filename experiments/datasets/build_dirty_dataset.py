"""Builds a multi-model instance whose cross-store divergence was authored by someone else.

The similarity experiment in experiments/run/similarity_sweep.py injects syntactic variation,
because neither instance we had contains any. Injected variation is a fair test of whether the
pipeline uses a similarity measure, but the rate and the corruption model are then ours, which is
exactly the assumption a reader should not have to take on trust.

'TPC-H Dirty' is a pre-existing corrupted variant of TPC-H distributed alongside the clean one. It
was not made for this paper. Measured against the clean source it corrupts every non-key attribute
at 3.0% (numeric ones near 10%) with exactly one character edit per corrupted value, and it never
touches a key. That gives both the variation and its controls for free.

The construction puts the same entities in two stores and draws them from the two variants:

    relational  customer, supplier, part          from the CLEAN source
    document    orders, embedding the customer    from the DIRTY variant
                and a line-item array embedding the part
    graph       Supplier nodes                    from the DIRTY variant

Every cross-store attribute pair is a true dependency by construction, since both sides describe
the same entity. Key pairs are controls: they must hold exactly under equality, because the dirty
variant leaves keys alone. Name, address and phone pairs must degrade under equality and recover
under edit distance with tau = 1.

The meta-level export is written directly rather than loaded into the three stores and exported
back out. That is the same artifact -- one observation set per scope, plus a catalog -- and the
store round trip is exercised by every other experiment here; repeating it would add a docker
dependency to an experiment about matching, not about storage.

usage: build_dirty_dataset.py [outDir] [--orders=N]
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

CLEAN = paths.DATA_ROOT / "TPC-H 1"
DIRTY = paths.DATA_ROOT / "TPC-H Dirty"
POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = Path(POSITIONAL[0]) if POSITIONAL else paths.WORK_ROOT / "dirty" / "metalevel"
ORDER_LIMIT = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--orders=")), "15000"))

TBL_COLUMNS = {
    "customer": ["c_custkey", "c_name", "c_address", "c_nationkey", "c_phone",
                 "c_acctbal", "c_mktsegment", "c_comment"],
    "supplier": ["s_suppkey", "s_name", "s_address", "s_nationkey", "s_phone",
                 "s_acctbal", "s_comment"],
    "part": ["p_partkey", "p_name", "p_mfgr", "p_brand", "p_type", "p_size",
             "p_container", "p_retailprice", "p_comment"],
    "orders": ["o_orderkey", "o_custkey", "o_orderstatus", "o_totalprice", "o_orderdate",
               "o_orderpriority", "o_clerk", "o_shippriority", "o_comment"],
    "lineitem": ["l_orderkey", "l_partkey", "l_suppkey", "l_linenumber", "l_quantity",
                 "l_extendedprice", "l_discount", "l_tax", "l_returnflag", "l_linestatus",
                 "l_shipdate", "l_commitdate", "l_receiptdate", "l_shipinstruct",
                 "l_shipmode", "l_comment"],
}

# Which attributes are carried into both stores, and whether the dirty variant corrupts them.
# The keys are the controls: a corrupted key would break the construction rather than test it.
SHARED = {
    "customer": [("c_custkey", False), ("c_name", True), ("c_address", True), ("c_phone", True)],
    "supplier": [("s_suppkey", False), ("s_name", True), ("s_address", True), ("s_phone", True)],
    "part": [("p_partkey", False), ("p_name", True), ("p_brand", True), ("p_container", True)],
}


def read_dirty(table):
    with (DIRTY / f"{table}.csv").open(encoding="utf-8", errors="replace", newline="") as handle:
        rows = [{k.lower(): v for k, v in row.items()} for row in csv.DictReader(handle)]
    key = TBL_COLUMNS[table][0]
    return {row[key]: row for row in rows}


def read_dirty_rows(table):
    """The dirty variant ships CSV with upper-case headers; the clean source ships pipe-separated
    .tbl with none. Both are normalised to lower-case column names here."""
    with (DIRTY / f"{table}.csv").open(encoding="utf-8", errors="replace", newline="") as handle:
        return [{k.lower(): v for k, v in row.items()} for row in csv.DictReader(handle)]


def read_clean(table, keys=None, limit=None):
    columns = TBL_COLUMNS[table]
    rows = []
    with (CLEAN / f"{table}.tbl").open(encoding="utf-8", errors="replace") as handle:
        for i, line in enumerate(handle):
            if limit is not None and i >= limit:
                break
            row = dict(zip(columns, line.rstrip("\n").rstrip("|").split("|")))
            if keys is None or row[columns[0]] in keys:
                rows.append(row)
    return rows


def write(name, scope, columns, rows, catalog):
    file_name = name.replace(":", "_").replace("@", "_at_") + ".csv"
    with (OUT / file_name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(c, "") for c in columns])
    catalog.append({"relation": name, "scope": scope, "rows": len(rows),
                    "columns": len(columns), "file": file_name})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    catalog = []

    dirty_customers = read_dirty("customer")
    dirty_suppliers = read_dirty("supplier")
    dirty_parts = read_dirty("part")

    # --- relational store: the clean readings of the shared entities -----------------------
    for table, store in (("customer", dirty_customers), ("supplier", dirty_suppliers),
                         ("part", dirty_parts)):
        columns = [c for c, _ in SHARED[table]]
        rows = read_clean(table, keys=set(store))
        write(f"public.{table}", "base", columns, rows, catalog)

    # --- document store: orders, embedding the dirty customer and dirty line-item parts -----
    # Orders and line items come from the dirty variant as well, because it is scale 0.001 while
    # the clean source is scale 1: drawing orders from the latter leaves almost none whose customer
    # exists in the former. Only their keys are used, and the dirty variant does not corrupt keys,
    # so this does not weaken the construction.
    orders = [o for o in read_dirty_rows("orders")
              if o["o_custkey"] in dirty_customers][:ORDER_LIMIT]
    order_keys = {o["o_orderkey"] for o in orders}

    lineitems = {}
    for row in read_dirty_rows("lineitem"):
        if row["l_orderkey"] in order_keys and row["l_partkey"] in dirty_parts:
            lineitems.setdefault(row["l_orderkey"], []).append(row)

    document_columns = ["_id", "o_custkey", "customer.name", "customer.address", "customer.phone"]
    document_rows = []
    for order in orders:
        dirty = dirty_customers[order["o_custkey"]]
        document_rows.append({
            "_id": order["o_orderkey"], "o_custkey": order["o_custkey"],
            "customer.name": dirty["c_name"], "customer.address": dirty["c_address"],
            "customer.phone": dirty["c_phone"],
        })
    write("shop.orders", "document", document_columns, document_rows, catalog)

    array_columns = ["order_line.l_partkey", "order_line.part.name",
                     "order_line.part.brand", "order_line.part.container"]
    array_rows = []
    for order in orders:
        for item in lineitems.get(order["o_orderkey"], []):
            dirty = dirty_parts[item["l_partkey"]]
            array_rows.append({
                "order_line.l_partkey": item["l_partkey"],
                "order_line.part.name": dirty["p_name"],
                "order_line.part.brand": dirty["p_brand"],
                "order_line.part.container": dirty["p_container"],
            })
    write("shop.orders@order_line", "order_line", array_columns, array_rows, catalog)

    # --- graph store: suppliers, dirty ------------------------------------------------------
    graph_columns = ["s_suppkey", "s_name", "s_address", "s_phone"]
    write("neo4j:Supplier", "base", graph_columns,
          [{c: row[c] for c in graph_columns} for row in dirty_suppliers.values()], catalog)

    body = ",\n".join("  " + json.dumps(entry) for entry in catalog)
    (OUT / "catalog.json").write_text("[\n" + body + "\n]\n", encoding="utf-8")

    # --- ground truth ------------------------------------------------------------------------
    expected = []
    pairs = [
        ("shop.orders", "o_custkey", "public.customer", "c_custkey", False),
        ("shop.orders", "customer.name", "public.customer", "c_name", True),
        ("shop.orders", "customer.address", "public.customer", "c_address", True),
        ("shop.orders", "customer.phone", "public.customer", "c_phone", True),
        ("shop.orders@order_line", "order_line.l_partkey", "public.part", "p_partkey", False),
        ("shop.orders@order_line", "order_line.part.name", "public.part", "p_name", True),
        ("shop.orders@order_line", "order_line.part.brand", "public.part", "p_brand", True),
        ("shop.orders@order_line", "order_line.part.container", "public.part",
         "p_container", True),
        ("neo4j:Supplier", "s_suppkey", "public.supplier", "s_suppkey", False),
        ("neo4j:Supplier", "s_name", "public.supplier", "s_name", True),
        ("neo4j:Supplier", "s_address", "public.supplier", "s_address", True),
        ("neo4j:Supplier", "s_phone", "public.supplier", "s_phone", True),
    ]
    for dependent_set, dependent, referenced_set, referenced, corrupted in pairs:
        expected.append({
            "dependent": f"{dependent_set}.{dependent}",
            "referenced": f"{referenced_set}.{referenced}",
            "corrupted_side": "document/graph reading comes from the dirty variant"
            if corrupted else "key, untouched by the dirty variant -- control",
            "corrupted": corrupted,
        })
    (OUT.parent / "ground_truth.json").write_text(
        json.dumps({"source": "TPC-H clean vs TPC-H Dirty", "dependencies": expected}, indent=2),
        encoding="utf-8")

    print(f"{len(catalog)} observation sets, "
          f"{sum(e['columns'] for e in catalog)} extractors -> {OUT}")
    for entry in catalog:
        print(f"  {entry['relation']:<28} scope={entry['scope']:<12} "
              f"{entry['rows']:>7,} rows x {entry['columns']}")
    print(f"\n{len(pairs)} expected cross-store dependencies, "
          f"{sum(1 for p in pairs if p[4])} of them over attributes the dirty variant corrupts")


if __name__ == "__main__":
    main()
