"""Renders benchmark/results/runs.jsonl as a comparison table.

Usage:
    python benchmark/report.py                 # show every variant
    python benchmark/report.py baseline optimized   # compare two variants
"""
import json
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths
RESULTS = paths.RESULTS / "spind-runs.jsonl"


def load():
    runs = OrderedDict()
    with RESULTS.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            # later runs of the same (variant, dataset) supersede earlier ones
            runs[(record["variant"], record["dataset"])] = record
    return runs


def fmt(value):
    return f"{value:,}"


def main():
    runs = load()
    wanted = sys.argv[1:]
    if wanted:
        # Argument order decides which variant is the baseline of the comparison; taking file
        # order instead silently inverts every ratio.
        variants = [v for v in wanted if any(v == recorded for (recorded, _d) in runs)]
    else:
        variants = []
        for (variant, _dataset) in runs:
            if variant not in variants:
                variants.append(variant)
    datasets = []
    for (_variant, dataset) in runs:
        if dataset not in datasets:
            datasets.append(dataset)

    if len(variants) < 2:
        variant = variants[0] if variants else None
        print(f"{'dataset':<14}{'algo ms':>10}{'candidates':>15}{'pINDs':>12}{'peak MB':>10}")
        print("-" * 61)
        for dataset in datasets:
            record = runs.get((variant, dataset))
            if not record:
                continue
            print(f"{dataset:<14}{fmt(record['algo_ms_min']):>10}{fmt(record['candidates_layer1']):>15}"
                  f"{fmt(record['pinds']):>12}{fmt(record['peak_heap_mb']):>10}")
        return

    base = variants[0]
    for other in variants[1:]:
        print(f"\n=== {other} vs {base} ===")
        header = (f"{'dataset':<14}{base[:11]+' ms':>14}{other[:11]+' ms':>14}"
                  f"{'speedup':>9}{'cand kept':>14}{'peak MB':>18}{'result':>9}")
        print(header)
        print("-" * len(header))
        for dataset in datasets:
            left = runs.get((base, dataset))
            right = runs.get((other, dataset))
            if not left or not right:
                continue
            speedup = left["algo_ms_min"] / right["algo_ms_min"] if right["algo_ms_min"] else float("nan")
            cand = (f"{right['candidates_layer1'] / left['candidates_layer1']:.0%}"
                    if left["candidates_layer1"] else "n/a")
            memory = f"{left['peak_heap_mb']:,}->{right['peak_heap_mb']:,}"
            same = "same" if left["digest"] == right["digest"] else "DIFFERS"
            print(f"{dataset:<14}{fmt(left['algo_ms_min']):>14}{fmt(right['algo_ms_min']):>14}"
                  f"{speedup:>8.2f}x{cand:>14}{memory:>18}{same:>9}")


def phases(variants):
    """Per-phase breakdown, to show *where* a variant's time went rather than just how much."""
    runs = load()
    datasets = []
    for (_variant, dataset) in runs:
        if dataset not in datasets:
            datasets.append(dataset)

    print(f"\n{'dataset':<14}{'variant':<20}{'chunk':>9}{'preprune':>10}{'sort':>9}{'merge':>9}{'validate':>10}{'inmem':>9}{'other':>9}")
    print("-" * 99)
    for dataset in datasets:
        shown = False
        for variant in variants:
            record = runs.get((variant, dataset))
            if not record:
                continue
            shown = True
            inmem = record.get("inmemory_ms", 0) + record.get("partitioned_ms", 0)
            accounted = (record["chunk_ms"] + record["prune_ms"] + record["sort_ms"]
                         + record["merge_ms"] + record["validate_ms"] + inmem)
            other = record["algo_ms_min"] - accounted
            print(f"{dataset if not shown or variant == variants[0] else '':<14}{variant:<20}"
                  f"{record['chunk_ms']:>9,}{record['prune_ms']:>10,}{record['sort_ms']:>9,}"
                  f"{record['merge_ms']:>9,}{record['validate_ms']:>10,}{inmem:>9,}{other:>9,}")
        if shown:
            print()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--phases":
        phases(sys.argv[2:] or ["baseline", "optimized"])
    else:
        main()
