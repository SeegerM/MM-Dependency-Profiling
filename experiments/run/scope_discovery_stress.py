"""Stress-tests union discovery: the shape rule against the demand-driven rule, case by case.

Each case is a tiny export of member scopes plus, where the case needs one, dependent scopes that
reference them. Both rules read the same export:

  shape    experiments/run/propose_unions.py: shared extractor core, complete disjoint key,
           one store, one sort
  demand   experiments/run/discover_scopes.py: a union only when a dependent needs it, i.e. no
           single same-sort key covers the dependent but a disjoint set of them does

Each case has one ground truth for both rules: the unions over which some inclusion holds that no
single scope states.

External cases can be added with --cases=<file.json> (format:
experiments/run/scope_discovery_cases.template.json); they are reported separately from the
built-in cases.

usage: scope_discovery_stress.py [--out=$WORK_ROOT/union-stress/scope-discovery-stress.txt]
                                 [--cases=<file.json>]
"""
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "experiments" / "run"))
sys.path.insert(0, str(REPO / "experiments" / "lib"))
import paths  # noqa: E402
from discover_scopes import discover  # noqa: E402
from propose_unions import load, proposals  # noqa: E402


def flag(name, default):
    prefix = f"--{name}="
    return next((a[len(prefix):] for a in sys.argv[1:] if a.startswith(prefix)), default)


def write_export(root, scopes):
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob("scope-*.csv"):
        old.unlink()
    catalog = []
    for index, (relation, columns, rows) in enumerate(scopes):
        name = f"scope-{index}.csv"
        with (root / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)
        catalog.append({"relation": relation, "file": name, "columns": len(columns), "rows": len(rows)})
    (root / "catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")


def col(*values):
    return [[v] for v in values]


ROUTER_SWITCH = {("id", ("neo4j:Router", "neo4j:Switch"))}


def cases():
    return {
        "positive-disjoint": {
            "scopes": [("neo4j:Router", ["id"], col("r1", "r2")),
                       ("neo4j:Switch", ["id"], col("s1", "s2")),
                       ("pg.log", ["device"], col("r1", "s2", "r2"))],
            "needed": ROUTER_SWITCH,
            "reason": "disjoint label keys, and a log whose device ids span both labels",
        },
        "no-dependent": {
            "scopes": [("neo4j:Router", ["id"], col("r1", "r2")),
                       ("neo4j:Switch", ["id"], col("s1", "s2"))],
            "needed": set(),
            "reason": "the same shape, but nothing references both labels, so no union is needed",
        },
        "single-member-suffices": {
            "scopes": [("neo4j:Router", ["id"], col("r1", "r2")),
                       ("neo4j:Switch", ["id"], col("s1", "s2")),
                       ("pg.log", ["device"], col("r1", "r2"))],
            "needed": set(),
            "reason": "the dependent is contained in one label; an ordinary inclusion states it",
        },
        "overlapping-members": {
            "scopes": [("neo4j:Router", ["id"], col("shared", "r2")),
                       ("neo4j:Switch", ["id"], col("shared", "s2")),
                       ("pg.log", ["device"], col("shared", "r2", "s2"))],
            "needed": set(),
            "reason": "overlap violates the coproduct precondition",
        },
        "coincidental-integers": {
            "scopes": [("neo4j:Order", ["id"], col("1", "2", "3", "4")),
                       ("neo4j:Product", ["id"], col("1", "2", "5", "6")),
                       ("pg.stock", ["qty"], col("1", "3", "5", "6"))],
            "needed": set(),
            "reason": "unrelated integer keys overlap, so no disjoint cover of the quantities exists",
        },
        "duplicate-key": {
            "scopes": [("neo4j:Router", ["id"], col("r1", "r1")),
                       ("neo4j:Switch", ["id"], col("s1", "s2")),
                       ("pg.log", ["device"], col("r1", "s1"))],
            "needed": set(),
            "reason": "the proposed target is not a key in every member",
        },
        "missing-key": {
            "scopes": [("neo4j:Router", ["id"], col("r1", "")),
                       ("neo4j:Switch", ["id"], col("s1", "s2")),
                       ("pg.log", ["device"], col("r1", "s1"))],
            "needed": set(),
            "reason": "the proposed target is not fully present",
        },
        "member-schema-superset": {
            "scopes": [("neo4j:Router", ["id", "kind"], [["r1", "router"], ["r2", "router"]]),
                       ("neo4j:Switch", ["id"], col("s1", "s2")),
                       ("pg.log", ["device"], col("r1", "s1"))],
            "needed": ROUTER_SWITCH,
            "reason": "a specialization may carry extra properties",
        },
        "mixed-node-edge": {
            "scopes": [("neo4j:Router", ["id"], col("r1", "r2")),
                       ("neo4j:Switch", ["id"], col("s1", "s2")),
                       ("neo4j:-[LINK]->", ["id"], col("e1", "e2")),
                       ("pg.log", ["device"], col("r1", "s1", "s2"))],
            "needed": ROUTER_SWITCH,
            "reason": "nodes and edges are not one population: the union of the two labels must "
                      "not absorb the edge, whose ids are also a disjoint key",
        },
        "spurious-shared-shape": {
            "scopes": [("neo4j:Employee", ["id"], col("employee-1", "employee-2")),
                       ("neo4j:Invoice", ["id"], col("invoice-1", "invoice-2")),
                       ("pg.payroll", ["employee"], col("employee-1", "employee-2")),
                       ("pg.ledger", ["invoice"], col("invoice-1"))],
            "needed": set(),
            "reason": "same key shape and disjoint ids, but each dependent references one label only",
        },
        "polymorphic-reference": {
            "scopes": [("doc.post", ["_id"], col("p1", "p2")),
                       ("doc.photo", ["_id"], col("f1", "f2")),
                       ("pg.comment", ["target"], col("p1", "f2", "f1"))],
            "needed": {("_id", ("doc.photo", "doc.post"))},
            "reason": "a comment references posts or photos: one union of two collections",
        },
        "fragmented-across-stores": {
            "scopes": [("pg.customer", ["id"], col("c1", "c2")),
                       ("mongo.customer", ["id"], col("c3", "c4")),
                       ("neo4j:-[BOUGHT]->", ["source.id"], col("c1", "c4", "c2"))],
            "needed": {("id", ("mongo.customer", "pg.customer"))},
            "reason": "one entity type split over two stores, referenced as one; the shape rule "
                      "never crosses stores",
        },
    }


def shape_rule(export):
    return {(c["column"], tuple(c["members"])) for c in proposals(load(export))}


def demand_rule(export):
    _, _, _, unions, _ = discover(export)
    return {(u["column"], tuple(sorted({m[0] for m in u["members"]}))) for u in unions}


def load_cases(path):
    """Cases from a JSON file: [{name, reason, scopes: [[relation, [columns], [[row], ...]]],
    needed: [[column, [member relations]]]}]."""
    out = {}
    for case in json.loads(Path(path).read_text(encoding="utf-8")):
        out[case["name"]] = {
            "scopes": [(r, cols, rows) for r, cols, rows in case["scopes"]],
            "needed": {(column, tuple(sorted(members))) for column, members in case["needed"]},
            "reason": case.get("reason", ""),
        }
    return out


def evaluate(work, case_set, prefix):
    counts = {"shape": [0, 0, 0], "demand": [0, 0, 0]}
    lines = ["  %-26s %-14s %-14s" % ("case", "shape TP/FP/FN", "demand TP/FP/FN")]
    for name, case in case_set.items():
        export = work / (prefix + name)
        write_export(export, case["scopes"])
        cells = []
        for rule, run in (("shape", shape_rule), ("demand", demand_rule)):
            actual, expected = run(export), case["needed"]
            tp, fp, fn = len(actual & expected), len(actual - expected), len(expected - actual)
            for i, n in enumerate((tp, fp, fn)):
                counts[rule][i] += n
            cells.append("%d/%d/%d" % (tp, fp, fn) + ("" if fp == fn == 0 else " !"))
        lines.append("  %-26s %-14s %-14s" % (name, cells[0], cells[1]))
        lines.append("    " + case["reason"])
    lines.append("")
    for rule, (tp, fp, fn) in counts.items():
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        lines.append("  %-6s precision %.3f (%d/%d)  recall %.3f (%d/%d)"
                     % (rule, precision, tp, tp + fp, recall, tp, tp + fn))
    return lines, counts


def main():
    work = paths.WORK_ROOT / "union-stress"
    out = Path(flag("out", str(work / "scope-discovery-stress.txt")))
    lines = ["# Union discovery stress test: shape rule vs demand-driven rule",
             "# Generated by experiments/run/scope_discovery_stress.py.", "",
             "## built-in cases (written with knowledge of both rules)"]
    built_in, counts = evaluate(work, cases(), "sd-")
    lines += built_in
    external = flag("cases", None)
    if external:
        lines += ["", "## external cases from %s" % external]
        more, _ = evaluate(work, load_cases(external), "sdx-")
        lines += more
    lines += ["", "  '!' marks a case where the rule disagrees with the expected outcome."]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    # Only the built-in cases gate the test run; external cases are evidence, reported as found.
    failed = counts["demand"][1] + counts["demand"][2]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
