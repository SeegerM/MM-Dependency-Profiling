"""Equality versus similarity matching under increasing syntactic variation.

For each perturbation rate this builds a perturbed copy of the meta-level export, runs SPIND twice
over it -- once with equality matching, once with edit-distance matching -- and scores both against
the UniBench-IND oracle. The comparison is on identical observations, so the only difference is
the matching relation.

usage: similarity_sweep.py [--rho=0.5] [--tau=1] [--rates=0,0.1,0.25,0.5,0.75,1.0]
  requires MM_CP; run  source core/adapters/build.sh  first.
"""
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


RHO = flag("rho", "0.5")
TAU = flag("tau", "1")
STORE = flag("store", "graph")
RATES = [float(r) for r in flag("rates", "0,0.1,0.25,0.5,0.75,1.0").split(",")]

BASE = paths.WORK_ROOT / "unibench"
CLEAN = BASE / "metalevel"
ORACLE = BASE / "unibench_oracle.json"
WORK = BASE / "similarity"

TRAILING_COMMA = re.compile(r",\s*([}\]])")
SCOPE_SUFFIX = re.compile(r"@[^.]*")


def discovered(result_dir):
    path = result_dir / "1-ary_pINDs.json"
    if not path.exists():
        return set()
    text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
    found = set()
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = f"{relation['relation']}.{'.'.join(pind['dependant'])}"
            for ref in pind["referenced"]:
                referenced = f"{ref['relation']}.{'.'.join(ref['attribute'])}"
                found.add((SCOPE_SUFFIX.sub("", dependent), SCOPE_SUFFIX.sub("", referenced)))
    return found


def run(main_class, export, result_dir, extra):
    subprocess.run(["java", "-Xmx8g", "-cp", os.environ["MM_CP"], main_class, str(export), RHO]
                   + extra + [str(BASE / "temp-sim"), str(result_dir)],
                   capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=REPO)
    return discovered(result_dir)


def main():
    if not os.environ.get("MM_CP"):
        sys.exit("MM_CP is not set: run  source core/adapters/build.sh  first")

    oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
    expected = {(d["dependent"], d["referenced"]) for d in oracle["dependencies"]}
    # Only dependencies touching the perturbed store can be affected; including the others would
    # dilute the effect with dependencies nothing was done to.
    affected = {pair for pair in expected if any("neo4j:" in side for side in pair)} \
        if STORE == "graph" else expected

    print(f"rho = {RHO}, tau = {TAU}, perturbed store = {STORE}")
    print(f"{len(expected)} oracle dependencies, {len(affected)} of them touching that store\n")
    print(f"  {'rate':>6} | {'equality':^30} | {'similarity, tau=' + TAU:^30}")
    print(f"  {'':>6} | {'recall(affected)  recall  prec':>30} "
          f"| {'recall(affected)  recall  prec':>30}")

    for rate in RATES:
        export = CLEAN if rate == 0 else WORK / f"rate-{rate}"
        if rate > 0:
            subprocess.run([sys.executable, str(REPO / "experiments/datasets/perturb_export.py"),
                            str(CLEAN), str(export), str(rate), f"--store={STORE}"],
                           capture_output=True, text=True, cwd=REPO)

        equality = run("SpindMetaLevel", export, WORK / f"eq-{rate}", [])
        similarity = run("SpindSimilarity", export, WORK / f"sim-{rate}", [TAU])

        row = f"  {rate:>6.2f} |"
        for found in (equality, similarity):
            hit_affected, hit_all = len(found & affected), len(found & expected)
            precision = hit_all / len(found) if found else 0.0
            row += (f" {hit_affected:>3}/{len(affected):<3} "
                    f" {hit_all:>3}/{len(expected):<3} {precision:>6.1%} |")
        print(row)

    print("\n  Recall is what the comparison is about. Precision is reported beside it because")
    print("  similarity admits matches equality rejects, and not all of them are correct --")
    print("  the robustness is bought, not free.")


if __name__ == "__main__":
    main()
