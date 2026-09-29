"""Checks the seeded TPC-H cross-model dependencies against a meta-level discovery run.

The generator computes each seeded degree from the data it wrote, so this compares a measured
coverage against a predicted one rather than against a hand-transcribed expectation. It also
counts how many discovered dependencies span two distinct stores, which is the figure the
cross-model evaluation reports.

usage: verify_tpch.py [rho] [logDir]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

RHO = sys.argv[1] if len(sys.argv) > 1 else "0.95"
LOGS = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(f"F:/temp/multimodel/compare/mm-{RHO}")
RESULTS = LOGS / "ind-results" / "1-ary_pINDs.json"
GROUND_TRUTH = paths.DATASET / "ground_truth.json"

TRAILING_COMMA = re.compile(r",\s*([}\]])")

# Which store an observation set belongs to. The scope suffix is dropped: a document and its
# embedded array are two scopes of one store, not two stores.
def store(relation):
    base = relation.split("@")[0]
    if base.startswith("neo4j:"):
        return "graph"
    if base.startswith("shop."):
        return "document"
    return "relational"


# The seeded expectations name attributes as the generator wrote them; the export names them by
# scope. Only the trailing attribute name is compared, which is unambiguous in this dataset.
def leaf(qualified):
    return qualified.split(".")[-1]


def discovered():
    text = TRAILING_COMMA.sub(r"\1", RESULTS.read_text(encoding="utf-8"))
    found = []
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            for ref in pind["referenced"]:
                found.append({
                    "dependent_relation": relation["relation"],
                    "dependent": ".".join(pind["dependant"]).split(".")[-1],
                    "referenced_relation": ref["relation"],
                    "referenced": ".".join(ref["attribute"]).split(".")[-1],
                    "degree": ref.get("partialDegree", 1.0),
                })
    return found


def main():
    found = discovered()
    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))

    cross = [f for f in found
             if store(f["dependent_relation"]) != store(f["referenced_relation"])]
    pairs = {}
    for f in cross:
        pairs.setdefault((store(f["dependent_relation"]), store(f["referenced_relation"])), 0)
        pairs[(store(f["dependent_relation"]), store(f["referenced_relation"]))] += 1

    print(f"rho = {RHO}")
    print(f"  psINDs discovered      {len(found)}")
    print(f"  spanning two stores    {len(cross)}")
    for (source, target), count in sorted(pairs.items(), key=lambda kv: -kv[1]):
        print(f"    {source:<11} -> {target:<11} {count}")

    print("\n  seeded dependencies:")
    ok = 0
    for expected in truth["expected_inds"]:
        dependent, referenced = leaf(expected["dependent"]), leaf(expected["referenced"])
        matches = [f for f in found
                   if f["dependent"] == dependent and f["referenced"] == referenced]
        predicted = expected["expected_coverage"]
        # A dependency whose parent field is read at document scope is predicted by the document
        # -scope figure the generator also computes; the array-item figure applies only when the
        # observation is materialized per element.
        alternative = expected.get("coverage_under_document_scope")
        if not matches:
            print(f"    MISSING  {dependent} -> {referenced}   (expected {predicted})")
            continue
        measured = max(m["degree"] for m in matches)
        candidates = [predicted] + ([alternative] if alternative is not None else [])
        agrees = any(abs(measured - c) < 1e-4 for c in candidates)
        ok += agrees
        note = "" if agrees else f"   != {' or '.join(str(c) for c in candidates)}"
        print(f"    {'ok ' if agrees else 'DIFF'}     {dependent} -> {referenced}"
              f"   measured {measured:.6f}   {expected['models']}{note}")
    print(f"\n  {ok}/{len(truth['expected_inds'])} seeded dependencies recovered at their "
          f"predicted degree")


if __name__ == "__main__":
    main()
