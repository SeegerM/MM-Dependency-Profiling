"""Discovers the union scopes an export needs, from the dependencies that need them.

A union is proposed only when some dependent needs it: no single key target covers the dependent
at rho, but a set of pairwise disjoint key targets from distinct scopes does. Targets are key
dimensions (distinct = filled = rows), and a target counts only if the dependent uses at least a
share u of its values, which separates a partition from a coincidental containment. Entities
(records, nodes) and relationships (edges) are never mixed unless --mixed-sorts is given. Covers
that share a member are merged when the result is still disjoint and of one population. Unions may
span stores.

With --materialize=<dir>, the export is copied there with every proposed union added as a scope,
in the format MetaLevelExporter.exportUnion writes.

usage: discover_scopes.py <exportDir> [--rho=1.0] [--u=0.5] [--min-member=1] [--mixed-sorts]
                          [--out=<report.txt>] [--json=<proposals.json>] [--materialize=<dir>]
"""
import csv
import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from propose_unions import sort_of, store_of  # noqa: E402

csv.field_size_limit(1 << 24)


def flag(name, default=None):
    prefix = "--" + name + "="
    return next((a[len(prefix):] for a in sys.argv[1:] if a.startswith(prefix)), default)


def load(export):
    """Per dimension (relation, column): its distinct values, filled count and the scope's rows."""
    catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    dims = {}
    for entry in catalog:
        # A union already in the export is an output of this rule, not an input to it.
        if entry.get("members"):
            continue
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
        for i, column in enumerate(header):
            dims[(entry["relation"], column)] = {"values": seen[i], "filled": filled[i], "rows": rows}
    return catalog, dims


def is_key(dim):
    return dim["rows"] > 0 and len(dim["values"]) == dim["filled"] == dim["rows"]


def population_of(relation):
    """Entities (records and nodes) or relationships (edges): what a union may not mix."""
    return "relationship" if sort_of(relation) == "edge" else "entity"


def covers(dims, rho, u, min_member, mixed_sorts):
    """The minimal union cover of every dependent that needs one."""
    targets = [d for d, dim in dims.items() if is_key(dim)]
    index = defaultdict(list)
    for target in targets:
        for value in dims[target]["values"]:
            index[value].append(target)

    found = []
    for dependent, dim in dims.items():
        values = dim["values"]
        if not values:
            continue
        hits = defaultdict(set)
        for value in values:
            for target in index.get(value, ()):
                if target[0] != dependent[0]:
                    hits[target].add(value)
        # Only targets the dependent substantially uses can explain it (rule 3).
        eligible = [t for t in hits if len(hits[t]) >= u * len(dims[t]["values"])]
        if not eligible:
            continue
        need = rho * len(values)
        # Covers are sought per population: a union is unnecessary only if a single target of the
        # same population already covers the dependent.
        pools = defaultdict(list)
        for target in eligible:
            pools["any" if mixed_sorts else population_of(target[0])].append(target)
        for pool in pools.values():
            # A target that covers the dependent alone states an ordinary inclusion; a union is
            # sought among the rest.
            pool = [t for t in pool if len(hits[t]) < need]
            if len(pool) < 2:
                continue
            chosen = best_cover(pool, hits, dims, need, min_member)
            if chosen is None:
                continue
            coverage = len(set().union(*(hits[c] for c in chosen))) / len(values)
            found.append({"dependent": dependent, "members": sorted(chosen), "coverage": coverage,
                          "distinct": len(values),
                          "best_single": max(len(hits[t]) for t in pool) / len(values)})
    return found


def best_cover(pool, hits, dims, need, min_member, restarts=20):
    """The preferred cover among greedy runs started from each of the best first members.

    One greedy run commits to its first member, and two disjoint covers can explain a dependent
    equally well. Among the covers found, the preferred one has the fewest stores, then the lowest
    total cost, then the fewest members.
    """
    cost = {t: 1 + len(dims[t]["values"]) - len(hits[t]) for t in pool}
    firsts = sorted(pool, key=lambda t: (-len(hits[t]) / cost[t], t))[:restarts]
    found = {}
    for first in firsts:
        chosen = greedy_cover(pool, hits, dims, need, min_member, first=first)
        if chosen:
            found[tuple(sorted(chosen))] = chosen
    if not found:
        return None
    return min(found.values(), key=lambda c: (len({store_of(t[0]) for t in c}),
                                              sum(cost[t] for t in c), len(c), sorted(c)))


def greedy_cover(pool, hits, dims, need, min_member, first=None):
    """A minimal set of pairwise disjoint targets from distinct scopes covering `need` values.

    Weighted set cover: each step takes the target with the most newly covered values per unit of
    cost, where cost is one plus the number of the target's values the dependent does not use.
    Ranking by coverage alone would let one large coincidental key win the first step and block
    the disjoint members of the true cover.
    """
    chosen, covered, used = [], set(), set()
    cost = {t: 1 + len(dims[t]["values"]) - len(hits[t]) for t in pool}
    candidates = sorted(pool, key=lambda t: (cost[t], -len(hits[t]), t))
    if first is not None:
        chosen, covered, used = [first], set(hits[first]), {first[0]}
    while len(covered) < need:
        best, gain, score = None, 0, 0.0
        for target in candidates:
            if target in chosen or target[0] in used:
                continue
            if any(dims[target]["values"] & dims[c]["values"] for c in chosen):
                continue
            new = len(hits[target] - covered)
            if new and new / cost[target] > score:
                best, gain, score = target, new, new / cost[target]
        if best is None or gain < min_member:
            return None
        chosen.append(best)
        used.add(best[0])
        covered |= hits[best]
    # Minimality: a member whose removal keeps the cover at rho is not needed.
    for member in sorted(chosen, key=lambda t: len(hits[t])):
        rest = set().union(*(hits[c] for c in chosen if c != member))
        if len(chosen) > 2 and len(rest) >= need:
            chosen.remove(member)
    return chosen if len(chosen) >= 2 else None


def merge(found, dims, mixed_sorts):
    """Merges covers that share a member into maximal disjoint unions of one population."""
    unions = []  # each: {"members": set, "dependents": [cover, ...]}
    for cover in sorted(found, key=lambda c: (-len(c["members"]), c["dependent"])):
        members = set(cover["members"])
        home = None
        for union in unions:
            if not union["members"] & members:
                continue
            merged = union["members"] | members
            sorts = {population_of(m[0]) for m in merged}
            disjoint = all(not (dims[a]["values"] & dims[b]["values"])
                           for a in merged for b in merged if a < b)
            scopes = [m[0] for m in merged]
            if disjoint and (mixed_sorts or len(sorts) == 1) and len(scopes) == len(set(scopes)):
                home = union
                union["members"] = merged
                break
        if home is None:
            home = {"members": members, "dependents": []}
            unions.append(home)
        home["dependents"].append(cover)
    for i, union in enumerate(sorted(unions, key=lambda u: sorted(u["members"])), 1):
        union["name"] = "discovered.union-%d" % i
        columns = {m[1] for m in union["members"]}
        union["column"] = columns.pop() if len(columns) == 1 else "value"
        union["stores"] = sorted({store_of(m[0]) for m in union["members"]})
        union["size"] = sum(len(dims[m]["values"]) for m in union["members"])
    return sorted(unions, key=lambda u: u["name"])


def materialize(export, catalog, unions, out):
    """Copies the export and adds each union as a scope, as MetaLevelExporter.exportUnion does."""
    if out.exists():
        raise SystemExit(f"{out} exists; refusing to overwrite")
    shutil.copytree(export, out)
    for union in unions:
        file = union["name"].replace(":", "_") + ".csv"
        rows = 0
        with (out / file).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([union["column"]])
            for relation, column in sorted(union["members"]):
                entry = next(e for e in catalog if e["relation"] == relation)
                with (export / entry["file"]).open(newline="", encoding="utf-8") as source:
                    reader = csv.reader(source)
                    position = next(reader).index(column)
                    for row in reader:
                        writer.writerow([row[position] if position < len(row) else ""])
                        rows += 1
        catalog.append({"relation": union["name"], "scope": "union", "rows": rows, "columns": 1,
                        "file": file, "members": sorted({m[0] for m in union["members"]}),
                        "member_columns": {m[0]: m[1] for m in union["members"]}})
    (out / "catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")


def report(export, dims, found, unions, rho, u, seconds):
    lines = [
        "# Union scopes discovered from the dependents that need them (experiments/run/discover_scopes.py).",
        "# Nothing below reads an oracle.",
        "",
        "export     %s" % export,
        "rho        %g (set coverage), u %g (utilisation)" % (rho, u),
        "dimensions %d, of which %d key targets" % (len(dims), sum(1 for d in dims.values() if is_key(d))),
        "covers     %d dependents need a union no single scope provides" % len(found),
        "unions     %d after merging" % len(unions),
        "time       %.2f s" % seconds,
        "",
    ]
    for union in unions:
        kind = "cross-store" if len(union["stores"]) > 1 else union["stores"][0]
        lines.append("%s  %d members, %d values, %s, column %s"
                     % (union["name"], len(union["members"]), union["size"], kind, union["column"]))
        for relation, column in sorted(union["members"]):
            lines.append("    member   %s.%s" % (relation, column))
        for cover in sorted(union["dependents"], key=lambda c: c["dependent"]):
            lines.append("    needed by %s.%s  coverage %.4f with %d members (best single %.4f)"
                         % (cover["dependent"][0], cover["dependent"][1], cover["coverage"],
                            len(cover["members"]), cover["best_single"]))
    if not unions:
        lines.append("none")
    return "\n".join(lines) + "\n"


def discover(export, rho=1.0, u=0.5, min_member=1, mixed_sorts=False):
    start = time.perf_counter()
    catalog, dims = load(export)
    found = covers(dims, rho, u, min_member, mixed_sorts)
    unions = merge(found, dims, mixed_sorts)
    return catalog, dims, found, unions, time.perf_counter() - start


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
        print(__doc__.strip().splitlines()[-2])
        return 2
    export = Path(sys.argv[1])
    rho, u = float(flag("rho", "1.0")), float(flag("u", "0.5"))
    catalog, dims, found, unions, seconds = discover(
        export, rho, u, int(flag("min-member", "1")), "--mixed-sorts" in sys.argv)
    text = report(export, dims, found, unions, rho, u, seconds)
    print(text)
    if flag("out"):
        Path(flag("out")).parent.mkdir(parents=True, exist_ok=True)
        Path(flag("out")).write_text(text, encoding="utf-8")
    if flag("json"):
        Path(flag("json")).write_text(json.dumps([
            {"name": u["name"], "column": u["column"], "stores": u["stores"], "size": u["size"],
             "members": [".".join(m) for m in sorted(u["members"])],
             "needed_by": [{"dependent": ".".join(c["dependent"]), "coverage": c["coverage"],
                            "members": [".".join(m) for m in c["members"]]}
                           for c in u["dependents"]]}
            for u in unions], indent=2), encoding="utf-8")
    if flag("materialize"):
        materialize(export, catalog, unions, Path(flag("materialize")))
        print("materialized ->", flag("materialize"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
