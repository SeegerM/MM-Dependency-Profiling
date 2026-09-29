"""Checks that the modified code, with every optimization flag off, still reproduces the results
of the original unmodified binary.

The discarded first session's `baseline` rows were produced by the pre-change code; the current
session's `baseline` rows by the changed code with flags defaulted off. Matching digests prove the
optimizations are genuinely opt-in and did not alter default behaviour.
"""
import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


def baselines(filename):
    path = RESULTS / filename
    return {
        record["dataset"]: record["digest"]
        for record in (json.loads(line) for line in path.open() if line.strip())
        if record["variant"] == "baseline"
    }


original = baselines("DISCARDED-cross-session-runs.jsonl")
current = baselines("runs.jsonl")

shared = sorted(set(original) & set(current))
mismatches = [dataset for dataset in shared if original[dataset] != current[dataset]]

for dataset in shared:
    verdict = "MISMATCH" if dataset in mismatches else "identical"
    print(f"{dataset:<14} {original[dataset][:12]}  {current[dataset][:12]}  {verdict}")

print()
print(f"{len(shared)} datasets compared, {len(mismatches)} mismatches")
raise SystemExit(1 if mismatches else 0)
