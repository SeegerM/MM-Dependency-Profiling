"""Checks a converted corpus against the foreign keys its source schema declares.

experiments/datasets/build_multimodel.py carries the corpus's published foreign keys into the
converted instance as ground truth. Both set and bag coverage are reported, since a key spread
across a collection-valued field can differ strongly between the two.

usage: verify_converted.py <instanceDir> [--rho=1.0] [--tau=] [--nary=1]
  instanceDir holds metalevel/ and ground_truth.json.
  requires MM_CP; run  source core/adapters/build.sh  first.
"""
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = Path(POSITIONAL[0])
EXPORT = BASE / "metalevel"
RHO, TAU, NARY = flag("rho", "1.0"), flag("tau", ""), flag("nary", "1")
TRAILING_COMMA = re.compile(r",\s*([}\]])")


def discovered(result_dir):
    found = {}
    for path in sorted(result_dir.glob("*ary_pINDs.json")):
        text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
        for relation in json.loads(text):
            for pind in relation.get("pINDs", []):
                dependent = f"{relation['relation']}.{'.'.join(pind['dependant'])}"
                for ref in pind["referenced"]:
                    referenced = f"{ref['relation']}.{'.'.join(ref['attribute'])}"
                    found[(dependent, referenced)] = ref.get("partialDegree", 1.0)
    return found


# One pass per observation set, not one per dependency: a single scope can hold tens of millions
# of rows that several dependencies read from.
_COLUMN_CACHE = {}
_needed = set()


def occurrences(qualified, catalog):
    if qualified in _COLUMN_CACHE:
        return _COLUMN_CACHE[qualified]
    for entry in catalog:
        if not qualified.startswith(entry["relation"] + "."):
            continue
        wanted = {q[len(entry["relation"]) + 1:]: q for q in _needed
                  if q.startswith(entry["relation"] + ".")}
        collected = {column: [] for column in wanted}
        with (EXPORT / entry["file"]).open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                for column, sink in collected.items():
                    value = row.get(column)
                    if value:
                        sink.append(value)
        for column, values in collected.items():
            _COLUMN_CACHE[wanted[column]] = values
        return _COLUMN_CACHE.get(qualified, [])
    return []


def coverages(dependent, referenced, catalog):
    left, right = occurrences(dependent, catalog), set(occurrences(referenced, catalog))
    if not left:
        return None, None
    distinct = set(left)
    return (len(distinct & right) / len(distinct),
            sum(1 for value in left if value in right) / len(left))


def main():
    if not os.environ.get("MM_CP"):
        sys.exit("MM_CP is not set: run  source core/adapters/build.sh  first")

    truth = json.loads((BASE / "ground_truth.json").read_text(encoding="utf-8"))
    catalog = json.loads((EXPORT / "catalog.json").read_text(encoding="utf-8"))
    # Every attribute any dependency will ask about, so one scan collects all of them.
    _needed.update(d["dependent"] for d in truth["dependencies"])
    _needed.update(d["referenced"] for d in truth["dependencies"])

    main_class = "SpindSimilarity" if TAU else "SpindMetaLevel"
    extra = [TAU] if TAU else []
    result_dir = BASE / (f"sim-{RHO}-t{TAU}" if TAU else f"eq-{RHO}-n{NARY}")
    command = (["java", "-Xmx8g", "-cp", os.environ["MM_CP"], main_class, str(EXPORT), RHO]
               + extra + [str(BASE / "temp"), str(result_dir)]
               + ([] if TAU else [NARY]))
    subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=REPO)
    found = discovered(result_dir)

    label = f"edit distance tau={TAU}" if TAU else f"equality, maxNary={NARY}"
    print(f"{truth['name']} from {truth['source']}   rho = {RHO}, {label}")
    print(f"{len(found)} inclusions discovered over {len(catalog)} observation sets\n")

    declared = truth["dependencies"]
    detailed = len(declared) <= 40
    if detailed:
        print(f"  {'declared foreign key':<64} {'set':>7} {'bag':>7} {'found':>6}")

    hits, misses, by_models = 0, [], {}
    for entry in declared:
        pair = (entry["dependent"], entry["referenced"])
        ok = pair in found
        hits += ok
        models = entry.get("models", "?")
        bucket = by_models.setdefault(models, [0, 0])
        bucket[0] += 1
        bucket[1] += ok
        if detailed:
            set_cov, bag_cov = coverages(*pair, catalog)
            covers = (f"{set_cov:>7.4f} {bag_cov:>7.4f}" if set_cov is not None
                      else f"{'--':>7} {'--':>7}")
            print(f"  {entry['dependent']} -> {entry['referenced']:<24}"[:64].ljust(64)
                  + f" {covers} {'yes' if ok else 'NO':>6}")
        elif not ok:
            misses.append(entry)

    if not detailed:
        print(f"  {'models':<26} {'declared':>9} {'recovered':>10}")
        for models, (total, ok) in sorted(by_models.items(), key=lambda kv: -kv[1][0]):
            print(f"  {models:<26} {total:>9} {ok:>10}")

    if misses:
        print(f"\n  not recovered ({len(misses)}):")
        for entry in misses[:15]:
            set_cov, bag_cov = coverages(entry["dependent"], entry["referenced"], catalog)
            covers = (f"set {set_cov:.4f} bag {bag_cov:.4f}" if set_cov is not None
                      else "attribute not in the export")
            print(f"    {entry['dependent']} -> {entry['referenced']}")
            print(f"      {covers}")
        if len(misses) > 15:
            print(f"    ... and {len(misses) - 15} more")

    print(f"\n  {hits}/{len(declared)} declared dependencies recovered "
          f"({hits / len(declared):.1%})")
    cross = [d for d in declared if d.get("cross_model", True)]
    cross_hits = sum(1 for d in cross if (d["dependent"], d["referenced"]) in found)
    print(f"  {cross_hits}/{len(cross)} of the cross-model ones "
          f"({cross_hits / len(cross):.1%})" if cross else "")
    print(f"  precision against this oracle: {hits}/{len(found)} = {hits / len(found):.3%}"
          if found else "")


if __name__ == "__main__":
    main()
