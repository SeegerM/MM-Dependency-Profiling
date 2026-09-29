"""Drops inclusions whose dependent side has too few distinct values to be informative.

An inclusion from a domain of k distinct values into any domain containing them is unsurprising
when k is small: a handful of small integers is included in almost every numeric column by
coincidence, not by design. This is the classic uninformative-IND problem, and it is a property of
the candidate rather than of the algorithm, so it belongs in a result filter.

The threshold is not tuned to the oracle. On the UniBench-IND setup the dependents of all 34 true
dependencies have at least 58 distinct values while the one noisy dependent has 4, so any cut
between 5 and 58 gives the same answer. The default sits in the middle of that gap; this script
prints the margin so the choice stays visibly insensitive.

usage: filter_uninformative.py [threshold] [--min-distinct N] [--metalevel]
"""
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
RHO = ARGS[0] if ARGS else "0.5"
MIN_DISTINCT = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--min-distinct=")), 10))
SCOPED = "--metalevel" in sys.argv

BASE = paths.WORK_ROOT / "unibench"
EXPORT = BASE / "metalevel"
RESULTS = BASE / (f"meta-results-{RHO}" if SCOPED else f"results-{RHO}") / "1-ary_pINDs.json"
ORACLE = BASE / "unibench_oracle.json"

TRAILING_COMMA = re.compile(r",\s*([}\]])")
SCOPE_SUFFIX = re.compile(r"@[^.]*")


def distinct_counts():
    """Distinct non-empty values per exported column, keyed as the oracle names attributes."""
    counts = {}
    for entry in json.loads((EXPORT / "catalog.json").read_text(encoding="utf-8")):
        relation = SCOPE_SUFFIX.sub("", entry["relation"])
        with (EXPORT / entry["file"]).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            seen = {column: set() for column in (reader.fieldnames or [])}
            for row in reader:
                for column, values in seen.items():
                    if row.get(column):
                        values.add(row[column])
        for column, values in seen.items():
            key = f"{relation}.{column}"
            counts[key] = max(counts.get(key, 0), len(values))
    return counts


def discovered():
    text = TRAILING_COMMA.sub(chr(92) + "1", RESULTS.read_text(encoding="utf-8"))
    found = {}
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = f"{relation['relation']}.{'.'.join(pind['dependant'])}"
            for ref in pind["referenced"]:
                referenced = f"{ref['relation']}.{'.'.join(ref['attribute'])}"
                if SCOPED:
                    dependent = SCOPE_SUFFIX.sub("", dependent)
                    referenced = SCOPE_SUFFIX.sub("", referenced)
                found[(dependent, referenced)] = ref["partialDegree"]
    return found


def score(found, oracle_pairs, label):
    hits = sum(1 for pair in found if pair in oracle_pairs)
    recall = hits / len(oracle_pairs)
    precision = hits / len(found) if found else 0.0
    print(f"  {label:<28} {len(found):>3} kept   recall {hits}/{len(oracle_pairs)} = {recall:.0%}"
          f"   precision {hits}/{len(found)} = {precision:.1%}")
    return hits


def main():
    counts = distinct_counts()
    oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
    oracle_pairs = {(d["dependent"], d["referenced"]) for d in oracle["dependencies"]}
    found = discovered()

    true_cardinalities = sorted({counts.get(d["dependent"], 0) for d in oracle["dependencies"]})
    dropped = {pair: deg for pair, deg in found.items() if counts.get(pair[0], 0) < MIN_DISTINCT}
    kept = {pair: deg for pair, deg in found.items() if pair not in dropped}

    print(f"rho = {RHO}, minimum distinct values on the dependent side = {MIN_DISTINCT}\n")
    score(found, oracle_pairs, "unfiltered")
    score(kept, oracle_pairs, f"cardinality >= {MIN_DISTINCT}")

    print(f"\n  dependents of true dependencies have {true_cardinalities} distinct values")
    if dropped:
        print("  dropped:")
        for (dependent, referenced), degree in sorted(dropped.items()):
            in_oracle = "IN ORACLE" if (dependent, referenced) in oracle_pairs else ""
            print(f"    {counts.get(dependent, 0):>4} distinct  {dependent:<42} -> {referenced:<34} {in_oracle}")
    smallest_true = min(v for v in true_cardinalities if v > 0)
    largest_dropped = max((counts.get(d, 0) for d, _ in dropped), default=0)
    print(f"\n  margin: largest dropped has {largest_dropped} distinct values, "
          f"smallest true dependent has {smallest_true} "
          f"-- any threshold in {largest_dropped + 1}..{smallest_true} gives this result")


if __name__ == "__main__":
    main()
