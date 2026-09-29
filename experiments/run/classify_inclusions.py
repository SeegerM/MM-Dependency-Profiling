"""Accounts for every discovered inclusion, rather than scoring it against a partial oracle.

Precision is the wrong instrument when the oracle is a corpus's foreign-key list. Such a list names
the dependencies a schema designer wrote down; it does not name every inclusion that holds. An
inclusion absent from it is unlabelled, not false, so a precision figure computed against it counts
correct results as errors and gets worse the more the algorithm finds.

Where the result set is small enough to enumerate -- which it is whenever the extractor family is
small, however large the data -- a complete accounting is available instead, and it says what a
percentage cannot: what the algorithm returned and why each item is there.

  declared        in the corpus's own foreign-key list
  restatement     the referenced side has the same value set as a declared key's referenced side,
                  so the inclusion restates a declared one. Two attributes mutually included in
                  each other denote one domain; every inclusion into either restates the other.
  cross-type      the dependent is numeric and the referenced side is not. These hold only because
                  values are compared as strings -- IMDB's 882 distinct running times all occur
                  among its title texts, since some films are called "1917". True, and an artifact
                  of untyped comparison rather than a property of the data.
  numeric-domain  both sides are numeric: a small integer range sits inside a larger one. True,
                  and unrelated -- a running time is not an episode number. Typing the comparison
                  domains would not remove these; only reasoning about interestingness would.
  vacuous         the dependent observation is empty, so the inclusion is a tautology
  key-to-key      both sides are near-unique; a genuine correspondence between identifier domains
  low-cardinality the dependent has few distinct values, so containment is unsurprising
  other           none of the above -- the residue worth reading

usage: classify_inclusions.py <instanceDir> <resultDir> [--low=25] [--key=0.95]
"""
import csv
import json
import re
import sys
from pathlib import Path

POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE, RESULTS = Path(POSITIONAL[0]), Path(POSITIONAL[1])
EXPORT = BASE / "metalevel"


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


LOW = int(flag("low", "25"))
KEY_RATIO = float(flag("key", "0.95"))
TRAILING_COMMA = re.compile(r",\s*([}\]])")


def discovered():
    found = {}
    for path in sorted(RESULTS.glob("*ary_pINDs.json")):
        text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
        for relation in json.loads(text):
            for pind in relation.get("pINDs", []):
                dependent = f"{relation['relation']}.{'.'.join(pind['dependant'])}"
                for ref in pind["referenced"]:
                    found[(dependent, f"{ref['relation']}.{'.'.join(ref['attribute'])}")] = \
                        ref.get("partialDegree", 1.0)
    return found


NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def statistics():
    """Distinct count, filled count and numeric share per attribute, one pass per set."""
    stats = {}
    for entry in json.loads((EXPORT / "catalog.json").read_text(encoding="utf-8")):
        with (EXPORT / entry["file"]).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            seen = {c: set() for c in columns}
            filled = {c: 0 for c in columns}
            numeric = {c: 0 for c in columns}
            for row in reader:
                for column in columns:
                    value = row.get(column)
                    if value:
                        seen[column].add(value)
                        filled[column] += 1
                        numeric[column] += bool(NUMERIC.match(value))
        for column in columns:
            share = numeric[column] / filled[column] if filled[column] else 0.0
            stats[f"{entry['relation']}.{column}"] = (len(seen[column]), filled[column], share)
    return stats


def equivalence_classes(found):
    """Attributes mutually included in each other denote one domain.

    Grouping them is what separates a genuine second finding from the same finding stated against
    a different name for the same value set -- on IMDB, Crew.tconst and Title.tconst hold the same
    9,890,756 identifiers, so every key into one appears again as a key into the other.
    """
    parent = {}

    def root(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for dependent, referenced in found:
        if (referenced, dependent) in found:
            a, b = root(dependent), root(referenced)
            if a != b:
                parent[a] = b
    return {x: root(x) for x in parent}


def main():
    truth = json.loads((BASE / "ground_truth.json").read_text(encoding="utf-8"))
    declared = {(d["dependent"], d["referenced"]) for d in truth["dependencies"]}
    found = discovered()
    stats = statistics()

    same_domain = equivalence_classes(set(found))
    declared_domains = {(same_domain.get(d, d), same_domain.get(r, r)) for d, r in declared}

    buckets = {"declared": [], "restatement": [], "cross-type": [], "numeric-domain": [],
               "vacuous": [], "key-to-key": [], "low-cardinality": [], "other": []}
    for pair, degree in found.items():
        distinct, filled, numeric = stats.get(pair[0], (0, 0, 0.0))
        _, _, referenced_numeric = stats.get(pair[1], (0, 0, 0.0))
        domains = (same_domain.get(pair[0], pair[0]), same_domain.get(pair[1], pair[1]))
        if pair in declared:
            buckets["declared"].append((pair, degree, distinct))
        elif distinct == 0:
            buckets["vacuous"].append((pair, degree, distinct))
        elif domains in declared_domains:
            buckets["restatement"].append((pair, degree, distinct))
        elif numeric > 0.99 and referenced_numeric < 0.5:
            buckets["cross-type"].append((pair, degree, distinct))
        elif numeric > 0.99 and referenced_numeric > 0.99 and distinct >= LOW:
            buckets["numeric-domain"].append((pair, degree, distinct))
        elif filled and distinct / filled >= KEY_RATIO and distinct > LOW:
            buckets["key-to-key"].append((pair, degree, distinct))
        elif distinct < LOW:
            buckets["low-cardinality"].append((pair, degree, distinct))
        else:
            buckets["other"].append((pair, degree, distinct))

    print(f"{truth['name']}: {len(found)} inclusions, "
          f"{len(declared)} declared foreign keys\n")
    for name, items in buckets.items():
        share = len(items) / len(found) if found else 0
        print(f"  {name:<16} {len(items):>5}  {share:>5.0%}")

    for name in ("other", "restatement", "cross-type", "numeric-domain", "key-to-key",
                 "low-cardinality"):
        items = buckets[name]
        if not items:
            continue
        print(f"\n  {name}:")
        for (dependent, referenced), degree, distinct in sorted(items, key=lambda i: -i[2])[:12]:
            print(f"    {distinct:>9,} distinct  {dependent} -> {referenced}"
                  + ("" if degree == 1.0 else f"   deg {degree:.4f}"))
        if len(items) > 12:
            print(f"    ... and {len(items) - 12} more")

    unlabelled = len(found) - len(buckets["declared"])
    print(f"\n  {len(buckets['declared'])} of {len(declared)} declared keys are among the "
          f"{len(found)} results;")
    print(f"  the remaining {unlabelled} are unlabelled rather than wrong, which is why a")
    print(f"  precision figure against this oracle would be misleading.")


if __name__ == "__main__":
    main()
