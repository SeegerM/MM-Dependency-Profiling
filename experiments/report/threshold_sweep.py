"""Threshold sensitivity of the remaining-mass bound.

The bound drops a candidate once its observed coverage plus everything still unprocessed cannot
reach the coverage its budget demands. Raising rho shrinks the budget, so candidates should become
impossible sooner. This prints removed-by-bound and runtime against rho to show whether that is
actually what happens.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths
RESULTS = paths.RESULTS / "spind-runs.jsonl"
ARTIFACTS = paths.RESULT_ROOT
RHOS = [("us-rho100", 1.00), ("us-rho99", 0.99), ("us-rho95", 0.95),
        ("us-rho90", 0.90), ("us-rho75", 0.75)]


def bound_stats(variant, dataset):
    """Pulls partition counters from the newest per-run metadata file.

    The harness JSON does not carry them, and defaulting a missing field to 0 silently reported
    'the bound never fired' for every threshold — which looked like a result rather than a gap.
    """
    folder = ARTIFACTS / f"{variant}_{dataset}"
    if not folder.is_dir():
        return {}
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if "removed_by_bound" in record:
            return record
    return {}

runs = {}
for line in RESULTS.open():
    line = line.strip()
    if line:
        record = json.loads(line)
        runs[(record["variant"], record["dataset"])] = record

print(f"{'rho':>6} {'candidates':>12} {'removed early':>14} {'% removed':>10} "
      f"{'baseline ms':>12} {'partitioned':>12} {'speedup':>8} {'pINDs':>9} result")
print("-" * 100)
for key, rho in RHOS:
    part = runs.get(("o5-partitioned", key))
    base = runs.get(("baseline", key))
    if not part:
        continue
    candidates = part["candidates_layer1"]
    removed = bound_stats("o5-partitioned", key).get("removed_by_bound", -1)
    share = removed / candidates if candidates else 0
    base_ms = base["algo_ms_min"] if base else 0
    speedup = base_ms / part["algo_ms_min"] if base and part["algo_ms_min"] else float("nan")
    same = "same" if base and base["digest"] == part["digest"] else "DIFFERS"
    print(f"{rho:>6.2f} {candidates:>12,} {removed:>14,} {share:>9.1%} "
          f"{base_ms:>12,} {part['algo_ms_min']:>12,} {speedup:>7.2f}x {part['pinds']:>9,} {same}")
