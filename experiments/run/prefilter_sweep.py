"""Applies foreign-key prefiltering to a discovery result and reports what survives.

A discovery run over a real schema returns far more inclusions than anyone can read: MusicBrainz
returns 198,059. The profile is meant to support user-informed decisions, so a result set that size
is not obviously better than the raw data it describes. The question is therefore not only how many
inclusions hold but how few a reader has to look at.

Two rules from the foreign-key detection literature apply directly here, and both consume metadata
the profile already carries:

  non-vacuous    an empty observation is contained in every other, so an inclusion with an empty
                 dependent is a tautology rather than a candidate.
  referenced key a foreign key references a primary key, which is a unique column combination
                 without nulls. Inclusions whose referenced side is not unique cannot be one
                 (Jiang and Naumann, HoPF).

Neither is a threshold, which matters: the minimum-cardinality filter this replaces has no
defensible cut on a real schema, because genuine relationship tables have single-valued dependent
columns. These two rules discard only candidates that are disqualified by definition.

usage: prefilter_sweep.py <instanceDir> <resultDir>
"""
import csv
import json
import re
import sys
from pathlib import Path

POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE, RESULTS = Path(POSITIONAL[0]), Path(POSITIONAL[1])
EXPORT = BASE / "metalevel"
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


def column_statistics():
    """distinct, non-null count and row count per attribute."""
    stats = {}
    for entry in json.loads((EXPORT / "catalog.json").read_text(encoding="utf-8")):
        with (EXPORT / entry["file"]).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            seen = {c: set() for c in columns}
            filled = {c: 0 for c in columns}
            rows = 0
            for row in reader:
                rows += 1
                for column in columns:
                    if row.get(column):
                        seen[column].add(row[column])
                        filled[column] += 1
        for column in columns:
            stats[f"{entry['relation']}.{column}"] = (len(seen[column]), filled[column], rows)
    return stats


def main():
    truth = json.loads((BASE / "ground_truth.json").read_text(encoding="utf-8"))
    oracle = {(d["dependent"], d["referenced"]) for d in truth["dependencies"]}
    found = discovered()
    stats = column_statistics()

    def is_key(attribute):
        """Unique and null-free: a primary key candidate, which is what a foreign key references."""
        distinct, filled, rows = stats.get(attribute, (0, 0, 0))
        return rows > 0 and distinct == rows and filled == rows

    def non_vacuous(pair):
        return stats.get(pair[0], (0, 0, 0))[0] > 0

    stages = [("all inclusions", found)]
    live = {p for p in found if non_vacuous(p)}
    stages.append(("non-vacuous dependent", live))
    keyed = {p for p in live if is_key(p[1])}
    stages.append(("referenced side is a key", keyed))

    print(f"{truth['name']}: {len(found):,} inclusions, {len(oracle)} declared foreign keys\n")
    print(f"  {'stage':<28} {'kept':>10} {'reduction':>10} {'recall':>16}")
    for name, kept in stages:
        hits = len(kept & oracle)
        print(f"  {name:<28} {len(kept):>10,} {len(found) / max(len(kept), 1):>9.1f}x "
              f"{hits:>6}/{len(oracle)} {hits / len(oracle):>6.0%}")

    lost = (stages[1][1] & oracle) - keyed
    if lost:
        print(f"\n  declared keys whose referenced side is not unique ({len(lost)}):")
        for dependent, referenced in sorted(lost)[:8]:
            distinct, filled, rows = stats.get(referenced, (0, 0, 0))
            print(f"    {dependent} -> {referenced}")
            print(f"      referenced: {distinct:,} distinct of {rows:,} rows, {filled:,} non-null")
        if len(lost) > 8:
            print(f"    ... and {len(lost) - 8} more")


if __name__ == "__main__":
    main()
