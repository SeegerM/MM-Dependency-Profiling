"""Checks the discovered cross-model INDs against the ground truth built into the dataset.

The reported partial degree equals the coverage: SPIND stores `1 - (1-theta) * violations/cap`
with `cap = (1-theta) * |A|`, which reduces to `1 - violations/|A|`. So a dependency injected at a
known coverage should be reported at that coverage, not merely be present.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

GROUND_TRUTH = paths.GROUND_TRUTH
CANONICAL = Path(sys.argv[1]) if len(sys.argv) > 1 else paths.canonical("baseline")

LINE = re.compile(r'^(?P<dep>.+?) -> (?P<ref>.+?) @(?P<degree>[0-9.]+)$')


def normalise(entry):
    """`public.customer.["c_custkey"]` -> `public.customer.c_custkey`"""
    return entry.replace('["', "").replace('"]', "")


def main():
    truth = json.loads(GROUND_TRUTH.read_text())
    found = {}
    for line in CANONICAL.read_text(encoding="utf-8").splitlines():
        match = LINE.match(line.strip())
        if match:
            key = (normalise(match["dep"]), normalise(match["ref"]))
            found[key] = float(match["degree"])

    print(f"{len(found)} pINDs discovered over {truth['orders']:,} order documents, "
          f"{truth['customers_kept']:,} customer rows, {truth['parts']:,} Part nodes\n")

    header = f"{'dependent':<38} {'referenced':<34} {'models':<24} {'expected':>9} {'found':>9}  ok"
    print(header)
    print("-" * len(header))

    failures = 0
    for expected in truth["expected_inds"]:
        key = (expected["dependent"], expected["referenced"])
        actual = found.get(key)
        ok = actual is not None and abs(actual - expected["expected_coverage"]) < 1e-3
        if not ok:
            failures += 1
        print(f"{expected['dependent']:<38} {expected['referenced']:<34} "
              f"{expected['models']:<24} {expected['expected_coverage']:>9.4f} "
              f"{(f'{actual:.4f}' if actual is not None else 'MISSING'):>9}  {'yes' if ok else 'NO'}")

    cross = sum(1 for (dep, ref) in found if store_of(dep) != store_of(ref))
    print(f"\n{cross} of {len(found)} discovered pINDs span two different stores")
    print(f"{len(truth['expected_inds']) - failures}/{len(truth['expected_inds'])} ground-truth "
          f"dependencies recovered at the expected coverage")
    return 1 if failures else 0


def store_of(entry):
    if entry.startswith("neo4j:"):
        return "graph"
    if entry.startswith("shop."):
        return "document"
    return "relational"


if __name__ == "__main__":
    sys.exit(main())
