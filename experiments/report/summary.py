"""Headline scalability table for the paper.

    python experiments/report/summary.py [baseline-variant] [comparison-variant]

Compares two variants on every dataset where both ran. Besides the total it reports the
*post-chunking* cost -- everything after the input has been read -- because the strategies do not
share phase names: sort-merge spends it in sort+merge+validate, the index strategies in a single
pass. Comparing "validation" alone would read as a 100% saving where the phase was replaced rather
than shrunk.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

RESULTS = paths.RESULTS / "spind-runs.jsonl"
ORDER = ["t2d", "webtables", "musicbrainz", "tpch1", "us", "tpch5", "imdb"]

BASE = sys.argv[1] if len(sys.argv) > 2 else "baseline"
BEST = sys.argv[2] if len(sys.argv) > 2 else "o5-partitioned"


def post_chunk(record):
    """Cost after reading the input, whichever phases the strategy uses."""
    return (record.get("sort_ms", 0) + record.get("merge_ms", 0) + record.get("validate_ms", 0)
            + record.get("inmemory_ms", 0) + record.get("partitioned_ms", 0))


runs = {}
for line in RESULTS.open():
    line = line.strip()
    if line:
        record = json.loads(line)
        runs[(record["variant"], record["dataset"])] = record

print(f"| {'dataset':<12} | {'relations':>9} | {BASE[:11]:>11} | {BEST[:11]:>11} | {'speedup':>7} "
      f"| {'post-chunk':>21} | result |")
print(f"|{'-'*14}|{'-'*11}|{'-'*13}|{'-'*13}|{'-'*9}|{'-'*23}|--------|")

for dataset in ORDER:
    base, best = runs.get((BASE, dataset)), runs.get((BEST, dataset))
    if not base or not best:
        continue
    speedup = base["algo_ms_min"] / best["algo_ms_min"]
    phase = f"{post_chunk(base):,}->{post_chunk(best):,} ms"
    same = "same" if base["digest"] == best["digest"] else "DIFFERS"
    print(f"| {dataset:<12} | {base['relations']:>9,} | {base['algo_ms_min']:>8,} ms | "
          f"{best['algo_ms_min']:>8,} ms | {speedup:>6.2f}x | {phase:>21} | {same:<6} |")

missing = [d for d in ORDER if not (runs.get((BASE, d)) and runs.get((BEST, d)))]
if missing:
    print(f"\nno {BASE}/{BEST} pair recorded for: {', '.join(missing)}")
