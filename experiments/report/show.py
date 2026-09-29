"""Dumps the raw recorded runs for one dataset: python benchmark/show.py <dataset>"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths
RESULTS = paths.RESULTS / "spind-runs.jsonl"
dataset = sys.argv[1] if len(sys.argv) > 1 else None

for line in RESULTS.open():
    line = line.strip()
    if not line:
        continue
    record = json.loads(line)
    if dataset and record["dataset"] != dataset:
        continue
    print(f"{record['variant']:<24} reps={record['repetitions']} "
          f"all={record['algo_ms_all']} min={record['algo_ms_min']:,} "
          f"removed={record.get('removed_by_bound', 'n/a')} "
          f"depth={record.get('partition_depth', 'n/a')} "
          f"parts={record.get('partitions', 'n/a')} "
          f"digest={record['digest'][:12]}")
