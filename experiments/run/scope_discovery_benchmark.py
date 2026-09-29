"""Controlled benchmark for union discovery: split a referenced scope, see whether the union returns.

TPC-H's multi-model export has references into three scopes whose population is known exactly:
parts and suppliers (graph labels, each referenced by a document path and an edge endpoint) and
customers (a relational table referenced by orders at coverage 0.9697, because the generator
deletes customers). Splitting one of them into k scopes by a hash of its key turns every such
reference into one that only the union of the k parts states, with ground truth known by
construction.

Each configuration varies one thing:

  k         number of parts (2 .. 32)
  skew      uniform hash partition, or Zipf-sized parts (part i gets a share proportional to 1/i),
            so the smallest parts hold a handful of rows and are easy to miss or to match by chance
  stores    parts stay in the scope's store, or alternate between it and a second store
            (horizontal fragmentation across stores)
  rho       1.0, and 0.95 for the customer reference, which no exact cover can reach

A configuration counts as recovered when discovery proposes exactly the k parts over the key
column. Every other proposed union is counted as spurious. Discovery reads the split export only;
the ground truth is used for scoring.

usage: scope_discovery_benchmark.py [--work=$WORK_ROOT] [--out=paper/tables/scope-discovery-benchmark.txt]
                                    [--quick]
"""
import csv
import json
import os
import shutil
import sys
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "experiments" / "lib"))
sys.path.insert(0, str(HERE / "experiments" / "run"))
import paths  # noqa: E402
from discover_scopes import discover  # noqa: E402

csv.field_size_limit(1 << 24)

# (label, scope to split, its key column, second store for the fragmented variant, dependents)
TARGETS = [
    ("Part", "neo4j:Part", "p_partkey", "shop",
     ["shop.orders@lineitems.lineitems.l_partkey", "neo4j:-[SUPPLIES]->.target.p_partkey"]),
    ("Supplier", "neo4j:Supplier", "s_suppkey", "shop",
     ["shop.orders@lineitems.lineitems.l_suppkey", "neo4j:-[SUPPLIES]->.source.s_suppkey"]),
    ("Customer", "public.customer", "c_custkey", "shop", ["shop.orders.o_custkey"]),
]


def flag(name, default=None):
    prefix = f"--{name}="
    return next((a[len(prefix):] for a in sys.argv[1:] if a.startswith(prefix)), default)


def configurations(quick):
    ks = [2, 8] if quick else [2, 4, 8, 16, 32]
    for label, relation, key, other, dependents in TARGETS:
        rhos = [1.0, 0.95] if label == "Customer" else [1.0]
        for rho in rhos:
            for k in ks:
                yield dict(label=label, relation=relation, key=key, other=other,
                           dependents=dependents, rho=rho, k=k, skew="uniform", stores="one")
            yield dict(label=label, relation=relation, key=key, other=other, dependents=dependents,
                       rho=rho, k=8, skew="zipf", stores="one")
            yield dict(label=label, relation=relation, key=key, other=other, dependents=dependents,
                       rho=rho, k=2, skew="uniform", stores="two")


def part_name(config, i):
    """The relation name of part i, in the naming the exporter uses for its store."""
    relation = config["relation"]
    if config["stores"] == "two" and i % 2 == 1:
        return "%s.%s_%d" % (config["other"], relation.split(":")[-1].split(".")[-1], i)
    return "%s_%d" % (relation, i)


def assign(values, k, skew):
    """Part index per key value: a hash partition, or Zipf shares over the hash order."""
    ordered = sorted(values, key=lambda v: zlib.crc32(v.encode("utf-8")))
    if skew == "uniform":
        return {v: zlib.crc32(v.encode("utf-8")) % k for v in ordered}
    weights = [1 / (i + 1) for i in range(k)]
    bounds, total, acc = [], sum(weights), 0.0
    for w in weights:
        acc += w / total
        bounds.append(acc * len(ordered))
    out, part = {}, 0
    for position, v in enumerate(ordered):
        while position >= bounds[part]:
            part += 1
        out[v] = part
    return out


def split_export(source, out, config):
    """Copies the export with config['relation'] replaced by k parts."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    catalog = json.loads((source / "catalog.json").read_text(encoding="utf-8"))
    entry = next(e for e in catalog if e["relation"] == config["relation"])
    with (source / entry["file"]).open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = list(reader)
    position = header.index(config["key"])
    parts = assign([r[position] for r in rows], config["k"], config["skew"])

    new_catalog = []
    for e in catalog:
        if e is entry:
            continue
        try:
            os.link(source / e["file"], out / e["file"])
        except OSError:
            shutil.copy2(source / e["file"], out / e["file"])
        new_catalog.append(e)
    members = []
    for i in range(config["k"]):
        name = part_name(config, i)
        file = name.replace(":", "_") + ".csv"
        chosen = [r for r in rows if parts[r[position]] == i]
        with (out / file).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(chosen)
        new_catalog.append(dict(entry, relation=name, file=file, rows=len(chosen)))
        members.append(name)
    (out / "catalog.json").write_text(json.dumps(new_catalog, indent=2), encoding="utf-8")
    sizes = sorted(sum(1 for r in rows if parts[r[position]] == i) for i in range(config["k"]))
    return sorted(members), sizes


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    out = Path(flag("out", str(HERE / "paper" / "tables" / "scope-discovery-benchmark.txt")))
    source = work / "mm_tpch" / "metalevel"
    scratch = work / "scope-bench"
    default_u = 0.5
    sweep = [0.0, 0.1, 0.25, 0.5, 0.75]
    lines = [
        "# Union discovery on TPC-H with one referenced scope split into k parts.",
        "# Generated by experiments/run/scope_discovery_benchmark.py; ground truth by construction.",
        "#",
        "# 'expected' is whether a union can hold at that rho: the customer reference covers 0.9697",
        "# of its values, so at rho = 1 the right answer is no union. 'correct' means the exact",
        "# k-part union was proposed when expected and not proposed when not.",
        "",
        "## per configuration, u = %g" % default_u,
        "  %-9s %5s %3s %-8s %-6s %-12s %8s %8s %7s %8s %7s" % (
            "scope", "rho", "k", "skew", "stores", "part sizes", "expected", "correct", "needed",
            "spurious", "time"),
    ]
    totals = {u: {"configs": 0, "correct": 0, "spurious": 0, "needed": 0, "of": 0} for u in sweep}
    for config in configurations("--quick" in sys.argv):
        export = scratch / "split"
        members, sizes = split_export(source, export, config)
        expected = not (config["label"] == "Customer" and config["rho"] == 1.0)
        for u in sweep:
            start = time.perf_counter()
            _, _, _, unions, _ = discover(export, rho=config["rho"], u=u)
            seconds = time.perf_counter() - start
            exact = [x for x in unions if sorted({m[0] for m in x["members"]}) == members
                     and x["column"] == config["key"]]
            needed = set()
            for x in exact:
                needed |= {".".join(c["dependent"]) for c in x["dependents"]}
            found_deps = sum(1 for d in config["dependents"] if d in needed)
            spurious = len(unions) - len(exact)
            correct = bool(exact) == expected
            t = totals[u]
            t["configs"] += 1
            t["correct"] += correct
            t["spurious"] += spurious
            if expected:
                t["needed"] += found_deps
                t["of"] += len(config["dependents"])
            if u != default_u:
                continue
            lines.append("  %-9s %5g %3d %-8s %-6s %-12s %8s %8s %7s %8d %6.1fs" % (
                config["label"], config["rho"], config["k"], config["skew"], config["stores"],
                "%d..%d" % (sizes[0], sizes[-1]), "yes" if expected else "no",
                "yes" if correct else "NO", "%d/%d" % (found_deps, len(config["dependents"])),
                spurious, seconds))
            for x in unions:
                if x not in exact:
                    lines.append("      spurious: %s, %d members: %s" % (
                        x["name"], len(x["members"]),
                        ", ".join(".".join(m) for m in sorted(x["members"]))[:110]))
    shutil.rmtree(scratch, ignore_errors=True)
    lines += ["", "## sensitivity to the utilisation threshold u",
              "  %5s %10s %10s %16s" % ("u", "correct", "spurious", "references found")]
    for u in sweep:
        t = totals[u]
        lines.append("  %5g %10s %10d %16s" % (u, "%d/%d" % (t["correct"], t["configs"]),
                                               t["spurious"], "%d/%d" % (t["needed"], t["of"])))
    lines += ["",
              "  'references found' counts the split scope's documented references among the",
              "  dependents that demand the recovered union, over configurations expecting one."]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
