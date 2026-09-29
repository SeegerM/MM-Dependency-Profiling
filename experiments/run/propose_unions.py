"""Proposes union scopes from an export, without consulting any oracle.

Candidates are derived from extractor names and value sets only:

  1. Group scopes by store and by the set of extractor names they carry; a scope whose extractor
     set is a superset of the group's core is absorbed (a specialization with extra attributes).
  2. Within a group, consider each extractor of the shared core as the union column.
  3. Keep it only if it is a key in every member (distinct = filled = rows).
  4. Keep it only if the members' value sets are pairwise disjoint.
  5. Require every member to be the same sort of population: all node labels, all relationship
     types, or all relational and document scopes.
  6. Require at least two members, and take the group at its maximal size.

usage: propose_unions.py <exportDir> [--out=paper/tables/union-candidates.txt] [--spec=<file>]
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
MIN_MEMBERS = 2


def flag(name, default=None):
    prefix = "--" + name + "="
    return next((a[len(prefix):] for a in sys.argv[1:] if a.startswith(prefix)), default)


def store_of(relation):
    """The store a scope came from, so a union is never proposed across two of them."""
    if relation.startswith("neo4j"):
        return "neo4j"
    head = relation.split(".", 1)[0]
    return head.split(":", 1)[0] or head


def sort_of(relation):
    """Node, edge or record: the sort of population a scope holds.

    The exporter writes a graph relationship type as `neo4j:-[type]->` and a node label as
    `neo4j:Label`.
    """
    if relation.startswith("neo4j:-["):
        return "edge"
    if relation.startswith("neo4j"):
        return "node"
    return "record"


def read_scope(export, entry):
    """Column names, and per column the distinct non-null values plus the filled and row counts."""
    with (export / entry["file"]).open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        seen, filled, rows = [set() for _ in header], [0] * len(header), 0
        for row in reader:
            rows += 1
            for i, value in enumerate(row[:len(header)]):
                if value != "":
                    filled[i] += 1
                    seen[i].add(value)
    return header, seen, filled, rows


def load(export):
    catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    scopes = {}
    for entry in catalog:
        # A union already in the export is an output of this rule, not an input to it.
        if entry["relation"].endswith("-union"):
            continue
        header, seen, filled, rows = read_scope(export, entry)
        scopes[entry["relation"]] = {
            "columns": header,
            "values": {name: seen[i] for i, name in enumerate(header)},
            "filled": {name: filled[i] for i, name in enumerate(header)},
            "rows": rows,
        }
    return scopes


def groups(scopes):
    """Scopes grouped by store and shared extractor core, superset members absorbed."""
    by_store = defaultdict(list)
    for relation, scope in scopes.items():
        by_store[(store_of(relation), sort_of(relation))].append(relation)

    found = []
    for (store, sort), members in by_store.items():
        # Seed a group from each distinct extractor set, largest first, so that the core of a group
        # is the set the most scopes agree on rather than whichever one was read first.
        cores = defaultdict(list)
        for relation in members:
            cores[frozenset(scopes[relation]["columns"])].append(relation)
        for core, seeds in sorted(cores.items(), key=lambda kv: (-len(kv[1]), sorted(kv[0]))):
            group = [r for r in members if core <= frozenset(scopes[r]["columns"])]
            if len(group) >= MIN_MEMBERS:
                found.append((store, sort, core, sorted(group)))
    # Drop a group that is contained in another with the same membership: the smaller core is the
    # one the rule proposes over, and both would produce the same union.
    unique, seen = [], set()
    for store, sort, core, group in found:
        key = (store, sort, tuple(group))
        if key not in seen:
            seen.add(key)
            unique.append((store, sort, core, group))
    return unique


def proposals(scopes):
    out = []
    for store, sort, core, group in groups(scopes):
        for column in sorted(core):
            values = [scopes[r]["values"][column] for r in group]
            keyed = all(len(scopes[r]["values"][column]) == scopes[r]["filled"][column]
                        == scopes[r]["rows"] for r in group)
            disjoint = True
            for i in range(len(values)):
                for j in range(i + 1, len(values)):
                    if values[i] & values[j]:
                        disjoint = False
                        break
                if not disjoint:
                    break
            if keyed and disjoint:
                out.append({"store": store, "sort": sort, "column": column, "members": group,
                            "size": sum(len(v) for v in values)})
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip().splitlines()[-1])
        return 2
    export = Path(sys.argv[1])
    scopes = load(export)
    found = proposals(scopes)

    lines = [
        "# Union scopes proposed from the export alone, by experiments/run/propose_unions.py.",
        "#",
        "# Nothing below reads an oracle. A candidate is a set of scopes from one store that share",
        "# an extractor core, over a column that is a key in every member and whose members' value",
        "# sets are pairwise disjoint. See the module docstring for why each condition is there.",
        "",
        "export   %s" % export,
        "scopes   %d considered" % len(scopes),
        "groups   %d shared-core groups of two or more" % len(groups(scopes)),
        "proposed %d union%s" % (len(found), "" if len(found) == 1 else "s"),
        "",
    ]
    for candidate in found:
        lines.append("  %s %ss over %s, %d members, %d values"
                     % (candidate["store"], candidate["sort"], candidate["column"], len(candidate["members"]),
                        candidate["size"]))
        for member in candidate["members"]:
            lines.append("      %s" % member)
    if not found:
        lines.append("  none")
    text = "\n".join(lines) + "\n"

    out = flag("out")
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    print(text)

    spec = flag("spec")
    if spec:
        # ExportSources' own `union` spec kind, so a proposal can be exported without editing Java.
        rows = ["union %s-union %s %s"
                % (c["store"], ",".join(c["members"]), c["column"]) for c in found]
        Path(spec).write_text("\n".join(rows) + "\n", encoding="utf-8")
        print("spec -> %s" % spec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
