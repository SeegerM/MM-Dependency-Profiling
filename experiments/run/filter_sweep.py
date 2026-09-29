"""How much of a large inclusion result survives an interestingness filter, and at what cost.

Discovery over a real schema returns far more inclusions than the schema declares foreign keys:
most columns of small cardinality are contained in most columns of large cardinality, by
coincidence rather than by design. That is a property of the candidates, not of the algorithm, so
it belongs in a result filter -- the same argument as experiments/run/filter_uninformative.py,
applied where the numbers are large enough to matter.

The sweep reports what a minimum-cardinality threshold on the dependent side costs and buys. Recall
is measured against the corpus's own foreign keys; the size of the surviving set says how much
remains for a human or a downstream tool to look at. A threshold is only defensible when there is a
range over which recall is flat, so the range is what this prints.

usage: filter_sweep.py <instanceDir> <resultDir> [--thresholds=1,2,5,10,...]
"""
import csv
import json
import re
import sys
from pathlib import Path

POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE, RESULTS = Path(POSITIONAL[0]), Path(POSITIONAL[1])
EXPORT = BASE / "metalevel"
THRESHOLDS = [int(t) for t in next(
    (a.split("=")[1] for a in sys.argv if a.startswith("--thresholds=")),
    "1,2,5,10,25,50,100,500,1000").split(",")]

TRAILING_COMMA = re.compile(r",\s*([}\]])")


def discovered():
    found = set()
    for path in sorted(RESULTS.glob("*ary_pINDs.json")):
        text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
        for relation in json.loads(text):
            for pind in relation.get("pINDs", []):
                dependent = f"{relation['relation']}.{'.'.join(pind['dependant'])}"
                for ref in pind["referenced"]:
                    found.add((dependent, f"{ref['relation']}.{'.'.join(ref['attribute'])}"))
    return found


def distinct_counts():
    counts = {}
    for entry in json.loads((EXPORT / "catalog.json").read_text(encoding="utf-8")):
        with (EXPORT / entry["file"]).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            seen = {c: set() for c in columns}
            for row in reader:
                for column in columns:
                    value = row.get(column)
                    if value:
                        seen[column].add(value)
        for column, values in seen.items():
            counts[f"{entry['relation']}.{column}"] = len(values)
    return counts


def main():
    truth = json.loads((BASE / "ground_truth.json").read_text(encoding="utf-8"))
    oracle = {(d["dependent"], d["referenced"]) for d in truth["dependencies"]}
    found = discovered()
    counts = distinct_counts()

    # An all-null column is vacuously included in everything, so it produces inclusions that are
    # true and say nothing. This is the same artifact the array-container column of nulls used to
    # produce, arriving here on its own: a real schema has nullable columns that happen to be
    # entirely unused. Separating them is not filtering, it is declining to count a tautology.
    vacuous = {pair for pair in found if counts.get(pair[0], 0) == 0}
    live = found - vacuous
    vacuous_keys = {pair for pair in oracle if counts.get(pair[0], 0) == 0}
    live_oracle = oracle - vacuous_keys

    print(f"{truth['name']}: {len(found):,} inclusions, {len(oracle)} declared foreign keys\n")
    print(f"  {len(vacuous):>9,} have an all-null dependent and are vacuously true")
    print(f"  {len(live):>9,} remain, against {len(live_oracle)} of the declared keys "
          f"({len(vacuous_keys)} of which are themselves over an all-null column)\n")

    print(f"  {'min distinct':>13} {'kept':>10} {'reduction':>10} {'recall':>14}")
    flat = []
    for threshold in THRESHOLDS:
        kept = {pair for pair in live if counts.get(pair[0], 0) >= threshold}
        hits = len(kept & live_oracle)
        print(f"  {threshold:>13,} {len(kept):>10,} {len(found) / max(len(kept), 1):>9.1f}x "
              f"{hits:>7}/{len(live_oracle)} {hits / len(live_oracle):>5.0%}")
        flat.append((threshold, hits, len(kept)))

    full = max(h for _, h, _ in flat)
    safe = [t for t, h, _ in flat if h == full]
    if len(safe) > 1:
        print(f"\n  recall is flat at {full}/{len(live_oracle)} for every threshold in "
              f"{min(safe)}..{max(safe)}")
    weakest = sorted((counts.get(d, 0), d, r) for d, r in live_oracle)[:3]
    print("\n  the declared keys with the fewest distinct dependent values:")
    for count, dependent, referenced in weakest:
        print(f"    {count:>7,}  {dependent} -> {referenced}")
    print("\n  A threshold above that count trades a real foreign key for a smaller result set.")


if __name__ == "__main__":
    main()
