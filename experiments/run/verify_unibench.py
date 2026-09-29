"""Scores SPIND's discovery against the UniBench-IND oracle.

Unlike the TPC-H-derived dataset, this ground truth was written by someone else, so recall here is
an external measurement rather than a check that we found what we planted. The oracle labels a
dependency as always holding or not always holding but gives no coverage value, so the measured
degree is reported alongside its label rather than compared to one.

usage: verify_unibench.py [threshold]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

THRESHOLD = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "1.0"
ORACLE = paths.WORK_ROOT / "unibench" / "unibench_oracle.json"
# A meta-level run reports each observation set under its scope, e.g. neo4j:Tag@vendors. The
# oracle names attributes without a scope, so the suffix is dropped when matching. That is a naming
# reconciliation, not a relaxation: the attribute is the same one either way.
SCOPED = "--metalevel" in sys.argv
RESULTS = (paths.WORK_ROOT / "unibench" /
           (f"meta-results-{THRESHOLD}" if SCOPED else f"results-{THRESHOLD}") / "1-ary_pINDs.json")
SCOPE_SUFFIX = re.compile(r"@[^.]*")



# SPIND's result writer emits `"attributes":[...],}` for any relation that contributed no pINDs --
# a trailing comma, so the file is not valid JSON. Present in the pinned commit and still present
# on the branch carrying the later work; reported upstream. Repair it rather than fail, so a
# reader of these results is not blocked on a writer fix.
TRAILING_COMMA = re.compile(r",\s*([}\]])")


def discovered():
    """SPIND's JSON output, flattened to {(dependent, referenced): degree}."""
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


def main():
    oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
    found = discovered()

    print(f"threshold {THRESHOLD}: {len(found)} pINDs discovered, "
          f"oracle has {len(oracle['dependencies'])} dependencies "
          f"({oracle['always_holds']} always hold, {oracle['does_not_always_hold']} do not)\n")

    header = f"{'':>5} {'label':<6} {'dependent':<44} {'referenced':<30} {'found':>8}"
    print(header); print("-" * len(header))

    hits = {"always": 0, "partial": 0}
    totals = {"always": 0, "partial": 0}
    for dep in oracle["dependencies"]:
        kind = "always" if dep["always_holds"] else "partial"
        totals[kind] += 1
        degree = found.get((dep["dependent"], dep["referenced"]))
        if degree is not None:
            hits[kind] += 1
        mark = "ok" if degree is not None else "--"
        shown = f"{degree:.4f}" if degree is not None else "miss"
        print(f"{mark:>5} {dep['label']:<6} {dep['dependent']:<44} {dep['referenced']:<30} {shown:>8}")

    print()
    for kind in ("always", "partial"):
        if totals[kind]:
            print(f"  recall, {kind:<7}: {hits[kind]}/{totals[kind]} = {hits[kind]/totals[kind]:.0%}")
    total_hits, total = sum(hits.values()), sum(totals.values())
    print(f"  recall, overall: {total_hits}/{total} = {total_hits/total:.0%}")
    print(f"  precision      : {total_hits}/{len(found)} = {total_hits/len(found):.1%}")


if __name__ == "__main__":
    main()
