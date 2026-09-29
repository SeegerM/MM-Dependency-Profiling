"""Equality versus edit distance on divergence a third party authored.

The instance built by experiments/datasets/build_dirty_dataset.py puts the same entities in two
stores, drawing one reading from the clean TPC-H source and the other from the pre-existing
'TPC-H Dirty' variant. Every cross-store pair is a true dependency by construction. Key pairs are
controls the dirty variant leaves alone; name, address and phone pairs carry its corruption.

What this adds over the injected-variation sweep is that neither the rate nor the corruption model
is ours. What it does not add is a different *kind* of corruption: the dirty variant applies single
character edits, so tau = 1 is again sufficient, and that remains a stated limitation rather than a
result.

usage: verify_dirty.py [--rho=0.5] [--tau=1]
  requires MM_CP; run  source core/adapters/build.sh  first.
"""
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

REPO = Path(__file__).resolve().parents[2]


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


RHO, TAU = flag("rho", "0.5"), flag("tau", "1")
BASE = paths.WORK_ROOT / "dirty"
EXPORT = BASE / "metalevel"
TRAILING_COMMA = re.compile(r",\s*([}\]])")


def discovered(result_dir):
    path = result_dir / "1-ary_pINDs.json"
    if not path.exists():
        return {}
    text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
    found = {}
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = f"{relation['relation']}.{'.'.join(pind['dependant'])}"
            for ref in pind["referenced"]:
                referenced = f"{ref['relation']}.{'.'.join(ref['attribute'])}"
                found[(dependent, referenced)] = ref.get("partialDegree", 1.0)
    return found


def run(main_class, result_dir, extra):
    subprocess.run(["java", "-Xmx8g", "-cp", os.environ["MM_CP"], main_class, str(EXPORT), RHO]
                   + extra + [str(BASE / "temp"), str(result_dir)],
                   capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=REPO)
    return discovered(result_dir)


def occurrences(qualified):
    """Every non-empty value of one extractor, with multiplicity."""
    for entry in json.loads((EXPORT / "catalog.json").read_text(encoding="utf-8")):
        if qualified.startswith(entry["relation"] + "."):
            column = qualified[len(entry["relation"]) + 1:]
            with (EXPORT / entry["file"]).open(encoding="utf-8", newline="") as handle:
                return [row[column] for row in csv.DictReader(handle) if row.get(column)]
    return []


def coverages(dependent, referenced):
    """Set and bag coverage of one dependency.

    Reporting both matters here more than anywhere else in this evaluation. Corruption at a fixed
    per-occurrence rate does not translate into a fixed set-coverage loss: on a low-cardinality
    attribute every corrupted occurrence contributes a *new* distinct value while the clean ones
    collapse into few, so the same 3% reads very differently under the two semantics.
    """
    left, right = occurrences(dependent), set(occurrences(referenced))
    if not left:
        return 0.0, 0.0
    distinct = set(left)
    return (len(distinct & right) / len(distinct),
            sum(1 for value in left if value in right) / len(left))


def main():
    if not os.environ.get("MM_CP"):
        sys.exit("MM_CP is not set: run  source core/adapters/build.sh  first")

    truth = json.loads((BASE / "ground_truth.json").read_text(encoding="utf-8"))
    equality = run("SpindMetaLevel", BASE / f"eq-{RHO}", [])
    similarity = run("SpindSimilarity", BASE / f"sim-{RHO}-t{TAU}", [TAU])

    print(f"rho = {RHO}, tau = {TAU}, source = {truth['source']}\n")
    print(f"  {'dependency':<58} {'set':>7} {'bag':>7} {'equality':>9} {'tau=' + TAU:>9}")

    groups = {True: [0, 0, 0], False: [0, 0, 0]}
    for entry in truth["dependencies"]:
        pair = (entry["dependent"], entry["referenced"])
        found_eq, found_sim = pair in equality, pair in similarity
        set_cov, bag_cov = coverages(*pair)
        bucket = groups[entry["corrupted"]]
        bucket[0] += 1
        bucket[1] += found_eq
        bucket[2] += found_sim
        label = f"{entry['dependent']} -> {entry['referenced']}"
        print(f"  {label:<58} {set_cov:>7.4f} {bag_cov:>7.4f} "
              f"{'yes' if found_eq else 'NO':>9} {'yes' if found_sim else 'NO':>9}")

    print()
    for corrupted, (total, eq, sim) in sorted(groups.items()):
        name = "corrupted by the dirty variant" if corrupted else "keys (control)"
        print(f"  {name:<32} {eq}/{total} under equality, {sim}/{total} under edit distance")


if __name__ == "__main__":
    main()
