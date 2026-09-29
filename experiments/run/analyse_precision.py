"""How much of the discovered cross-model IND set is actually meaningful?

Recall was 6/6. Precision was not measured, and 96 dependencies were reported. This computes
per-extractor uniqueness the way a UCC discovery would see it, then asks how many of the 96
survive the classical foreign-key condition -- inclusion into a unique column -- and whether the
seeded dependencies are among the survivors.

It also computes uniqueness twice for the document store: once over the tabularized view the
adapter produces, and once at document scope. Those disagree, and the disagreement matters.
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

DATA = paths.DATASET
CANONICAL = paths.canonical("baseline")
GROUND_TRUTH = json.loads((DATA / "ground_truth.json").read_text())

LINE = re.compile(r'^(?P<dep>.+?) -> (?P<ref>.+?) @(?P<degree>[0-9.]+)$')


def csv_columns(path, prefix):
    """distinct/total per column of a CSV, keyed as prefix.column."""
    stats = defaultdict(lambda: [set(), 0])
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            for column, value in row.items():
                if value is None or value == "":
                    continue
                stats[f"{prefix}.{column}"][0].add(value)
                stats[f"{prefix}.{column}"][1] += 1
    return {k: (len(v[0]), v[1]) for k, v in stats.items()}


def mongo_columns():
    """Uniqueness under both scopes: one row per array element, and one row per document."""
    exploded = defaultdict(lambda: [set(), 0])
    document = defaultdict(lambda: [set(), 0])
    for line in (DATA / "mongo_orders.json").open(encoding="utf-8"):
        doc = json.loads(line)
        lineitems = doc.get("lineitems") or [{}]
        scalars = {k: v for k, v in doc.items() if k != "lineitems"}
        for key, value in scalars.items():
            document[f"shop.orders.{key}"][0].add(str(value))
            document[f"shop.orders.{key}"][1] += 1
        for item in lineitems:
            for key, value in scalars.items():
                exploded[f"shop.orders.{key}"][0].add(str(value))
                exploded[f"shop.orders.{key}"][1] += 1
            for key, value in item.items():
                exploded[f"shop.orders.lineitems.{key}"][0].add(str(value))
                exploded[f"shop.orders.lineitems.{key}"][1] += 1
    return ({k: (len(v[0]), v[1]) for k, v in exploded.items()},
            {k: (len(v[0]), v[1]) for k, v in document.items()})


def main():
    stats = {}
    stats.update(csv_columns(DATA / "pg_customer.csv", "public.customer"))
    stats.update(csv_columns(DATA / "pg_nation.csv", "public.nation"))
    stats.update(csv_columns(DATA / "pg_region.csv", "public.region"))
    stats.update(csv_columns(DATA / "neo4j_part.csv", "neo4j:Part"))
    stats.update(csv_columns(DATA / "neo4j_supplier.csv", "neo4j:Supplier"))
    exploded, document = mongo_columns()
    stats.update(exploded)

    def unique(name):
        entry = stats.get(name)
        return entry is not None and entry[0] == entry[1] and entry[1] > 0

    discovered = []
    for line in CANONICAL.read_text(encoding="utf-8").splitlines():
        match = LINE.match(line.strip())
        if match:
            dep = match["dep"].replace('["', "").replace('"]', "")
            ref = match["ref"].replace('["', "").replace('"]', "")
            discovered.append((dep, ref, float(match["degree"])))

    seeded = {(e["dependent"], e["referenced"]) for e in GROUND_TRUTH["expected_inds"]}
    # An extractor whose observation is empty is vacuously included in everything. Definition 6
    # already excludes it by requiring a non-empty dependent value set, and the admissible family
    # is defined over leaf paths -- an array container is neither. Both guards are in the paper and
    # neither is in the adapter, so apply them here and report the effect separately.
    def empty(name):
        entry = stats.get(name)
        return entry is None or entry[1] == 0

    non_empty = [(d, r, g) for (d, r, g) in discovered if not empty(d) and not empty(r)]
    survivors = [(d, r, g) for (d, r, g) in non_empty if unique(r)]
    seeded_kept = sum(1 for (d, r, _) in survivors if (d, r) in seeded)
    seeded_non_empty = sum(1 for (d, r, _) in non_empty if (d, r) in seeded)

    print(f"discovered psINDs                          : {len(discovered)}")
    print(f"  of which seeded (true positives)         : {len(seeded)}")
    print(f"  precision without a key condition        : {len(seeded)}/{len(discovered)} "
          f"= {len(seeded) / len(discovered):.1%}")
    print()
    print(f"after excluding empty observations         : {len(non_empty)}")
    print(f"  seeded dependencies retained             : {seeded_non_empty}/{len(seeded)}")
    print(f"  precision                                : {seeded_non_empty}/{len(non_empty)} "
          f"= {seeded_non_empty / len(non_empty):.1%}")
    print()
    print(f"+ requiring the referenced side unique     : {len(survivors)}")
    print(f"  seeded dependencies retained             : {seeded_kept}/{len(seeded)}")
    print(f"  precision with the key condition         : {seeded_kept}/{len(survivors)} "
          f"= {seeded_kept / len(survivors):.1%}" if survivors else "  no survivors")
    print()
    print("surviving dependencies:")
    for dep, ref, degree in sorted(survivors):
        mark = "seeded" if (dep, ref) in seeded else "      "
        print(f"  {mark}  {dep:<38} -> {ref:<34} @{degree:.4f}")

    print()
    print("uniqueness of document keys depends on the scope:")
    for key in ("shop.orders.o_orderkey", "shop.orders.o_custkey"):
        e_d, e_t = exploded.get(key, (0, 0))
        d_d, d_t = document.get(key, (0, 0))
        print(f"  {key:<28} tabularized {e_d:>7,}/{e_t:<7,} unique={e_d == e_t:<5} | "
              f"document {d_d:>7,}/{d_t:<7,} unique={d_d == d_t}")


if __name__ == "__main__":
    main()
