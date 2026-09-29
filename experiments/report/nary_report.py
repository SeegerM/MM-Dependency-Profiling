"""Reports n-ary INDs from a meta-level run, separating those that span two stores.

Everything else in this evaluation is unary, which is where the framework is easiest to exercise
and where the interesting cases are not. A composite inclusion across stores is the multi-model
form of a composite foreign key, and it is also what a join-induced scope needs when the join
predicate is not a single attribute.

usage: nary_report.py <resultDir>
"""
import json
import re
import sys
from pathlib import Path

RESULTS = Path(sys.argv[1])
# SPIND's writer emits "attributes":[...],} for a relation contributing no pINDs, so the n-ary
# files are not valid JSON. Repaired while reading rather than worked around by the caller.
TRAILING_COMMA = re.compile(r",\s*([}\]])")


def store_of(relation):
    base = relation.split("@")[0]
    if base.startswith("neo4j:"):
        return "graph"
    if base.startswith("unibench.") or base.startswith("shop."):
        return "document"
    return "relational"


def main():
    print(f"{RESULTS}\n")
    print(f"  {'arity':>5} {'total':>7} {'cross-store':>12} {'partial':>8}")
    listings = []
    for path in sorted(RESULTS.glob("*ary_pINDs.json"),
                       key=lambda p: int(re.match(r"(\d+)", p.name).group(1))):
        arity = int(re.match(r"(\d+)", path.name).group(1))
        text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
        total = cross = partial = 0
        rows = []
        for relation in json.loads(text):
            for pind in relation.get("pINDs", []):
                for ref in pind["referenced"]:
                    total += 1
                    spans = store_of(relation["relation"]) != store_of(ref["relation"])
                    degree = ref.get("partialDegree", 1.0)
                    cross += spans
                    partial += degree < 1.0
                    if spans and arity > 1:
                        rows.append((f"{relation['relation']}({', '.join(pind['dependant'])})",
                                     f"{ref['relation']}({', '.join(ref['attribute'])})", degree))
        print(f"  {arity:>5} {total:>7} {cross:>12} {partial:>8}")
        listings.append((arity, rows))

    for arity, rows in listings:
        if not rows:
            continue
        print(f"\n  cross-store INDs of arity {arity}:")
        for dependent, referenced, degree in sorted(rows, key=lambda r: -r[2]):
            marker = "     " if degree == 1.0 else "     "
            print(f"  {marker}{dependent}")
            print(f"        -> {referenced}   degree {degree:.4f}")


if __name__ == "__main__":
    main()
