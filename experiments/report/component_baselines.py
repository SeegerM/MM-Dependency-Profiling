"""Report the UniBench representation-by-relaxation component ablation.

The four cells use the same unary SPIND implementation and vary only:

* representation: the direct, relation-shaped adapters or the scope-aware export; and
* validity: exact INDs (rho=1) or partial INDs (rho=0.5).

The UniBench oracle is external to this project.  Its omissions are therefore unlabelled rather
than known false positives; the reported ``oracle precision`` is agreement with that curated list.

By default all discovery outputs are read below WORK_ROOT/unibench.  A separate scoped root is
useful when regenerating configuration-matched cells without overwriting archived runs:

    python experiments/report/component_baselines.py \
        --scoped-root tmp/baselines
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths


TRAILING_COMMA = re.compile(r",\s*([}\]])")
SCOPE_SUFFIX = re.compile(r"@[^.]*")


def result_path(root, representation, rho):
    prefix = "results" if representation == "flat" else "meta-results"
    return root / f"{prefix}-{rho}" / "1-ary_pINDs.json"


def load_discovered(path, scoped):
    """Return SPIND output as one canonical pair per candidate.

    Scoped relation names carry ``@scope`` while the external oracle does not.  Removing that
    suffix reconciles names only; it does not change the observed values or the validity degree.
    """
    text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
    found = {}
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = f"{relation['relation']}.{'.'.join(pind['dependant'])}"
            for reference in pind["referenced"]:
                referenced = f"{reference['relation']}.{'.'.join(reference['attribute'])}"
                if scoped:
                    dependent = SCOPE_SUFFIX.sub("", dependent)
                    referenced = SCOPE_SUFFIX.sub("", referenced)
                key = (dependent, referenced)
                degree = float(reference["partialDegree"])
                if key in found and found[key] != degree:
                    raise ValueError(f"conflicting degrees for {key}: {found[key]} and {degree}")
                found[key] = degree
    return found


def attribute_count(folder):
    """Read the extractor count from the newest SPIND run record, when present."""
    records = sorted(folder.glob("SPIND_*.json"), key=lambda path: path.stat().st_mtime,
                     reverse=True)
    for record in records:
        try:
            metadata = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "attributes" in metadata:
            return int(metadata["attributes"])
    return None


def pct(numerator, denominator):
    return 100.0 * numerator / denominator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = paths.WORK_ROOT / "unibench"
    parser.add_argument("--flat-root", type=Path, default=default_root,
                        help="root containing results-1.0 and results-0.5")
    parser.add_argument("--scoped-root", type=Path, default=default_root,
                        help="root containing meta-results-1.0 and meta-results-0.5")
    args = parser.parse_args()

    oracle_path = default_root / "unibench_oracle.json"
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    expected = {(entry["dependent"], entry["referenced"])
                for entry in oracle["dependencies"]}

    settings = [
        ("flat", "1.0", "flat adapter", "exact"),
        ("flat", "0.5", "flat adapter", "partial (rho=0.5)"),
        ("scoped", "1.0", "scoped lift", "exact"),
        ("scoped", "0.5", "scoped lift", "partial (rho=0.5)"),
    ]
    rows = []
    for representation, rho, label, validity in settings:
        root = args.flat_root if representation == "flat" else args.scoped_root
        path = result_path(root, representation, rho)
        found = load_discovered(path, scoped=representation == "scoped")
        hits = len(expected.intersection(found))
        rows.append({
            "representation": representation,
            "label": label,
            "validity": validity,
            "rho": rho,
            "attributes": attribute_count(path.parent),
            "candidates": len(found),
            "hits": hits,
        })

    # A component ablation is interpretable only if each representation is held fixed across rho.
    for representation in ("flat", "scoped"):
        counts = {row["attributes"] for row in rows
                  if row["representation"] == representation and row["attributes"] is not None}
        if len(counts) > 1:
            raise SystemExit(
                f"incomparable {representation} runs: extractor counts are {sorted(counts)}; "
                "regenerate both thresholds from the same export"
            )

    total = len(expected)
    print(f"UniBench component baselines ({total} externally labelled dependencies)")
    print(f"{'representation':<14} {'validity':<20} {'candidates':>10} "
          f"{'oracle hits':>12} {'recall':>9} {'oracle precision':>18}")
    print("-" * 90)
    for row in rows:
        recall = pct(row["hits"], total)
        precision = pct(row["hits"], row["candidates"])
        print(f"{row['label']:<14} {row['validity']:<20} {row['candidates']:>10} "
              f"{row['hits']:>5}/{total:<6} {recall:>8.1f}% {precision:>17.1f}%")

    by_key = {(row["representation"], row["rho"]): row for row in rows}
    flat_exact = by_key[("flat", "1.0")]
    flat_partial = by_key[("flat", "0.5")]
    scoped_exact = by_key[("scoped", "1.0")]
    scoped_partial = by_key[("scoped", "0.5")]
    print("\ncomponent effects")
    print(f"  scoped lift at rho=1.0 : {flat_exact['hits']} -> {scoped_exact['hits']} hits; "
          f"{flat_exact['candidates']} -> {scoped_exact['candidates']} candidates")
    print(f"  relaxation, flat input : {flat_exact['hits']} -> {flat_partial['hits']} hits")
    print(f"  relaxation, scoped input: {scoped_exact['hits']} -> {scoped_partial['hits']} hits")
    print(f"  combined                : {flat_exact['hits']}/{total} -> "
          f"{scoped_partial['hits']}/{total} oracle recall")


if __name__ == "__main__":
    main()
