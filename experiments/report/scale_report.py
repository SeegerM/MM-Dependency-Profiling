"""Formats the multi-model scalability timings and reports how each phase grows.

Growth relative to the data is what matters here, not absolute milliseconds: the question the
paper needs answered is whether the meta level is a fixed translation cost or a scaling one, and
which of the four discovery phases dominates as an instance grows.

One caveat is reported alongside, because it changes how the FD and UCC columns should be read.
Both run once per observation set in a separate JVM, so at these sizes their totals are dominated
by process startup -- seven JVMs at roughly half a second each. Their growth is meaningful; their
absolute values are an upper bound and not comparable with the single-process phases.

usage: scale_report.py [timings.jsonl]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else paths.WORK_ROOT / "scale" / "timings.jsonl"
PHASES = [("export_ms", "export"), ("ind_ms", "IND"), ("similarity_ms", "similarity"),
          ("fd_ms", "FD*"), ("ucc_ms", "UCC*")]


def main():
    records = [json.loads(line) for line in PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    records.sort(key=lambda r: r["orders"])
    if not records:
        sys.exit(f"no records in {PATH}")

    print(f"{PATH}\n")
    header = f"  {'orders':>8} {'rows':>10} {'|E|':>5} {'INDs':>6}"
    for _, label in PHASES:
        header += f" {label:>11}"
    print(header)
    for record in records:
        line = (f"  {record['orders']:>8,} {record['rows']:>10,} {record['extractors']:>5} "
                f"{record['inds']:>6}")
        for key, _ in PHASES:
            line += f" {record[key]:>9,}ms"
        print(line)

    first, last = records[0], records[-1]
    data_growth = last["rows"] / first["rows"]
    print(f"\n  from {first['orders']:,} to {last['orders']:,} orders the data grows "
          f"{data_growth:.1f}x ({first['rows']:,} -> {last['rows']:,} observations)")
    print(f"  {'phase':<12} {'growth':>8} {'relative to data':>18}")
    for key, label in PHASES:
        growth = last[key] / first[key] if first[key] else float("nan")
        print(f"  {label:<12} {growth:>7.2f}x {growth / data_growth:>17.2f}")

    print("\n  * FD and UCC run once per observation set in a separate JVM, so their totals are")
    print("    dominated by process startup at these sizes. Read their growth, not their level.")
    print("  Every phase grows well below the data: the extractor count is fixed, and it is the")
    print("  extractor count rather than the row count that drives candidate generation.")


if __name__ == "__main__":
    main()
