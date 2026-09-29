"""Generates the Heterogeneity Profile (Tab. 1) from discovery results.

Everything is read off the IND, UCC and FD results plus the extractor catalog. Components:

  schema-level      cross-store links, and how many join differently named extractors
  data-level        links whose coverage falls short of 1
  syntactic         links that equality misses and edit-distance matching finds
  structural        UCCs and FDs per store, and whether a store nests
  join candidates   links whose referenced side is a key

Coverage uses set semantics (over supp(L), not weighted by multiplicity), because bag coverage
changes with the scope at which a nested store is read.

usage: build_profile.py <exportDir> <indResultDir> [--ucc=<uccLog>] [--fd=<fdLog>]
                        [--similarity=<simResultDir>] [--json=<out>]
"""
import csv
import json
import re
import sys
from pathlib import Path

TRAILING_COMMA = re.compile(r",\s*([}\]])")
SCOPE_SUFFIX = re.compile(r"@[^.]*")
SET_NAME = re.compile(r"^--- (.+)$")
COUNT = re.compile(r"^(FDs|UCCs)\s*:\s*(\d+)")


def flag(name, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


# Store per observation set, from the catalog; store_of() falls back to a name-prefix rule.
STORES = {}


def store_of(relation):
    if relation in STORES:
        return STORES[relation]
    base = relation.split("@")[0]
    if base in STORES:
        return STORES[base]
    if base.startswith("neo4j:"):
        return "graph"
    if base.startswith("unibench.") or base.startswith("shop.") or base.startswith("mongo."):
        return "document"
    return "relational"


def leaf(column):
    return column.split(".")[-1]


def levenshtein(a, b):
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


# ------------------------------------------------------------------------------------- inputs

def read_export(export):
    """{relation -> (columns, {column -> set of distinct non-empty values})} plus row counts."""
    sets = {}
    for entry in json.loads((export / "catalog.json").read_text(encoding="utf-8")):
        if "store" in entry:
            STORES[entry["relation"]] = entry["store"]
        with (export / entry["file"]).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            values = {column: set() for column in columns}
            rows = 0
            for row in reader:
                rows += 1
                for column in columns:
                    if row.get(column):
                        values[column].add(row[column])
        sets[entry["relation"]] = {"columns": columns, "values": values, "rows": rows,
                                   "scope": entry.get("scope", "base"), "file": entry["file"]}
    return sets


def read_inds(result_dir):
    path = result_dir / "1-ary_pINDs.json"
    if not path.exists():
        return []
    text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
    links = []
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = ".".join(pind["dependant"])
            for ref in pind["referenced"]:
                links.append((relation["relation"], dependent,
                              ref["relation"], ".".join(ref["attribute"])))
    return links


def read_counts(path):
    """{observation set file -> count} from an fd.log or ucc.log written by run_all.sh."""
    if not path or not Path(path).exists():
        return {}
    counts, current = {}, None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        name = SET_NAME.match(line)
        if name:
            current = name.group(1)
            continue
        match = COUNT.match(line)
        if match and current:
            counts[current] = int(match.group(2))
    return counts


# ------------------------------------------------------------------------------------ profile

def set_coverage(sets, link):
    """Coverage of one link, or None when the dependent observation is empty.

    An empty observation is contained in every other, so the inclusion is vacuous rather than a
    dependency at coverage zero.
    """
    dep_relation, dep_column, ref_relation, ref_column = link
    dependent = sets[dep_relation]["values"].get(dep_column, set())
    referenced = sets[ref_relation]["values"].get(ref_column, set())
    if not dependent:
        return None
    return len(dependent & referenced) / len(dependent)


def main():
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    export, ind_dir = Path(positional[0]), Path(positional[1])
    sets = read_export(export)
    links = [l for l in read_inds(ind_dir) if l[0] in sets and l[2] in sets]
    similarity_dir = flag("similarity")
    similarity_links = ([l for l in read_inds(Path(similarity_dir)) if l[0] in sets and l[2] in sets]
                        if similarity_dir else [])
    ucc_counts, fd_counts = read_counts(flag("ucc")), read_counts(flag("fd"))

    # ---- per store
    stores = {}
    for relation, data in sets.items():
        entry = stores.setdefault(store_of(relation), {
            "scopes": 0, "extractors": 0, "rows": 0, "nested": False, "uccs": 0, "fds": 0})
        entry["scopes"] += 1
        entry["extractors"] += len(data["columns"])
        entry["rows"] += data["rows"]
        entry["nested"] |= "@" in relation
        # Keyed by exact file name: neo4j_Tag.csv is a prefix of neo4j_Tag@vendors.csv.
        entry["uccs"] += ucc_counts.get(data["file"], 0)
        entry["fds"] += fd_counts.get(data["file"], 0)

    print("Heterogeneity Profile")
    print(f"  export {export}")
    # An exact IND run cannot show data-level variation (every link has coverage 1).
    print(f"  inclusions from {ind_dir}\n")
    print(f"  {'store':<12} {'scopes':>7} {'extractors':>11} {'rows':>10} {'nested':>7}"
          f" {'UCCs':>6} {'FDs':>6}")
    for name, data in sorted(stores.items()):
        print(f"  {name:<12} {data['scopes']:>7} {data['extractors']:>11} {data['rows']:>10,}"
              f" {'yes' if data['nested'] else 'no':>7} {data['uccs']:>6} {data['fds']:>6}")

    # ---- keys, needed for the join-candidate component
    keys = set()
    for relation, data in sets.items():
        for column in data["columns"]:
            values = data["values"][column]
            if values and len(values) == data["rows"]:
                keys.add((relation, column))

    # ---- per ordered store pair
    pairs = {}
    for link in links:
        source, target = store_of(link[0]), store_of(link[2])
        if source == target:
            continue
        entry = pairs.setdefault((source, target), {
            "links": 0, "renamed": 0, "coverage": [], "joins": 0, "similarity_only": 0,
            "vacuous": 0})
        coverage = set_coverage(sets, link)
        if coverage is None:
            entry["vacuous"] += 1
            continue
        entry["links"] += 1
        entry["coverage"].append(coverage)
        if leaf(link[1]) != leaf(link[3]):
            entry["renamed"] += 1
        if (link[2], link[3]) in keys:
            entry["joins"] += 1

    equality_set = set(links)
    for link in similarity_links:
        source, target = store_of(link[0]), store_of(link[2])
        if source == target or link in equality_set:
            continue
        entry = pairs.setdefault((source, target), {
            "links": 0, "renamed": 0, "coverage": [], "joins": 0, "similarity_only": 0,
            "vacuous": 0})
        entry["similarity_only"] += 1

    print(f"\n  {'store pair':<24} {'links':>7} {'vacuous':>8} {'renamed':>8} {'min cov':>8}"
          f" {'mean cov':>9} {'partial':>8} {'joins':>7} {'sim-only':>9}")
    for (source, target), data in sorted(pairs.items()):
        coverage = data["coverage"] or [0.0]
        partial = sum(1 for c in coverage if c < 1.0)
        print(f"  {source + ' -> ' + target:<24} {data['links']:>7} {data['vacuous']:>8}"
              f" {data['renamed']:>8} {min(coverage):>8.4f}"
              f" {sum(coverage) / len(coverage):>9.4f}"
              f" {partial:>8} {data['joins']:>7} {data['similarity_only']:>9}")

    vacuous = sum(d["vacuous"] for d in pairs.values())
    total = sum(d["links"] for d in pairs.values())
    renamed = sum(d["renamed"] for d in pairs.values())
    joins = sum(d["joins"] for d in pairs.values())
    partial = sum(1 for d in pairs.values() for c in d["coverage"] if c < 1.0)
    sim_only = sum(d["similarity_only"] for d in pairs.values())

    print(f"\n  schema-level      {renamed} of {total} cross-store links join differently named "
          f"extractors")
    print(f"  data-level        {partial} of {total} hold only partially under set semantics")
    # An upper bound: on a clean instance this count is the similarity measure's false-positive
    # floor.
    print(f"  syntactic         {sim_only} links admitted by edit distance and not by equality "
          f"(upper bound; compare against the clean-instance floor)")
    print(f"  structural        {sum(1 for d in stores.values() if d['nested'])} of {len(stores)} "
          f"stores nest; "
          + ", ".join(f"{n}: {d['uccs']} UCCs / {d['fds']} FDs over {d['extractors']} extractors"
                      for n, d in sorted(stores.items())))
    print(f"  join candidates   {joins} of {total} links reference a key")

    out = flag("json")
    if out:
        Path(out).write_text(json.dumps({
            "stores": stores,
            "pairs": {f"{s}->{t}": {**d, "coverage": None,
                                    "min_coverage": min(d["coverage"] or [0.0]),
                                    "mean_coverage": (sum(d["coverage"]) / len(d["coverage"]))
                                    if d["coverage"] else 0.0}
                      for (s, t), d in pairs.items()},
            "summary": {"links": total, "renamed": renamed, "partial": partial,
                        "similarity_only": sim_only, "join_candidates": joins},
        }, indent=2), encoding="utf-8")
        print(f"\n  written to {out}")


if __name__ == "__main__":
    main()
