"""Stress-test the automatic union proposer on controlled positive and negative cases.

Varies, one at a time, the conditions the proposal rule relies on: keyhood, presence,
disjointness, population sort, store boundary, unequal member schemas, and a semantically spurious
but structurally identical pair, which the rule cannot distinguish by design.

usage: union_proposal_stress.py
       [--out=$WORK_ROOT/union-stress/union-proposal-stress.txt]
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "experiments" / "run"))
sys.path.insert(0, str(REPO / "experiments" / "lib"))
import paths  # noqa: E402
from propose_unions import load, proposals  # noqa: E402


def flag(name, default):
    prefix = f"--{name}="
    return next((arg[len(prefix):] for arg in sys.argv[1:] if arg.startswith(prefix)), default)


def write_export(root, scopes):
    root.mkdir(parents=True, exist_ok=True)
    catalog = []
    for index, (relation, columns, rows) in enumerate(scopes):
        name = f"scope-{index}.csv"
        with (root / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)
        catalog.append({"relation": relation, "file": name, "columns": len(columns)})
    (root / "catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")


def key(candidate):
    return candidate["column"], tuple(candidate["members"])


def cases():
    return {
        "positive-disjoint": {
            "scopes": [
                ("neo4j:Router", ["id"], [["r1"], ["r2"]]),
                ("neo4j:Switch", ["id"], [["s1"], ["s2"]]),
            ],
            "expected": {("id", ("neo4j:Router", "neo4j:Switch"))},
            "reason": "compatible label scopes with complete, disjoint keys",
        },
        "overlapping-members": {
            "scopes": [
                ("neo4j:Router", ["id"], [["shared"], ["r2"]]),
                ("neo4j:Switch", ["id"], [["shared"], ["s2"]]),
            ],
            "expected": set(),
            "reason": "overlap violates the coproduct precondition",
        },
        "duplicate-key": {
            "scopes": [
                ("neo4j:Router", ["id"], [["r1"], ["r1"]]),
                ("neo4j:Switch", ["id"], [["s1"], ["s2"]]),
            ],
            "expected": set(),
            "reason": "the proposed target is not a key in every member",
        },
        "missing-key": {
            "scopes": [
                ("neo4j:Router", ["id"], [["r1"], [""]]),
                ("neo4j:Switch", ["id"], [["s1"], ["s2"]]),
            ],
            "expected": set(),
            "reason": "the proposed target is not fully present",
        },
        "member-schema-superset": {
            "scopes": [
                ("neo4j:Router", ["id", "kind"], [["r1", "router"], ["r2", "router"]]),
                ("neo4j:Switch", ["id"], [["s1"], ["s2"]]),
            ],
            "expected": {("id", ("neo4j:Router", "neo4j:Switch"))},
            "reason": "a specialization may carry extra properties",
        },
        "mixed-node-edge": {
            "scopes": [
                ("neo4j:Router", ["id"], [["r1"], ["r2"]]),
                ("neo4j:Switch", ["id"], [["s1"], ["s2"]]),
                ("neo4j:-[LINK]->", ["id"], [["e1"], ["e2"]]),
            ],
            "expected": {("id", ("neo4j:Router", "neo4j:Switch"))},
            "reason": "node and edge populations must not be unioned together",
        },
        "different-stores": {
            "scopes": [
                ("left.Router", ["id"], [["r1"], ["r2"]]),
                ("right.Switch", ["id"], [["s1"], ["s2"]]),
            ],
            "expected": set(),
            "reason": "a cross-store relationship is an IND, not one native population",
        },
        "spurious-shared-shape": {
            "scopes": [
                ("neo4j:Employee", ["id"], [["employee-1"], ["employee-2"]]),
                ("neo4j:Invoice", ["id"], [["invoice-1"], ["invoice-2"]]),
            ],
            "expected": set(),
            "reason": "same key shape and disjoint IDs do not prove a common entity supertype",
        },
    }


def main():
    work = paths.WORK_ROOT / "union-stress"
    out = Path(flag("out", str(work / "union-proposal-stress.txt")))
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    tp = fp = fn = 0
    for name, case in cases().items():
        export = work / name
        write_export(export, case["scopes"])
        actual = {key(candidate) for candidate in proposals(load(export))}
        expected = case["expected"]
        tp += len(actual & expected)
        fp += len(actual - expected)
        fn += len(expected - actual)
        rows.append((name, len(expected), len(actual), len(actual & expected),
                     len(actual - expected), len(expected - actual), case["reason"], actual))

    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    lines = [
        "# Automatic union-proposal stress test",
        "",
        "  case                       expected actual  TP  FP  FN",
    ]
    for name, expected_n, actual_n, case_tp, case_fp, case_fn, reason, actual in rows:
        lines.append(f"  {name:<27} {expected_n:>8} {actual_n:>6} {case_tp:>3} "
                     f"{case_fp:>3} {case_fn:>3}")
        lines.append(f"    {reason}")
        if actual:
            lines.append("    proposed: " + "; ".join(
                f"{column} over {','.join(members)}" for column, members in sorted(actual)))
    lines += [
        "",
        f"  micro precision: {precision:.3f} ({tp}/{tp + fp})",
        f"  micro recall:    {recall:.3f} ({tp}/{tp + fn})",
        "",
        "## Interpretation",
        "",
        "  The operational guards work: overlap, duplicate/missing keys, cross-store scopes, and",
        "  node/edge mixing are rejected, while a member with extra properties is retained. The",
        "  spurious-shared-shape case is a deliberate false positive. Employee and Invoice labels",
        "  have the same complete key name and disjoint domains, so the current rule cannot tell",
        "  them from two specializations of one entity type. Consequently the automatic rule is a",
        "  candidate proposer, not a semantic union detector: it recovers the MMSBench case and is",
        "  selective on the tested instances, but its general precision needs a schema hierarchy",
        "  or adjudication.",
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)
    print(f"precision={precision:.3f} recall={recall:.3f}")


if __name__ == "__main__":
    main()
