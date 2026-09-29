"""Discovers functional dependencies over join-induced scopes that span two stores.

An FD whose determinant and dependent live in different stores cannot be stated over a single
scope, but it can over the join-induced scope p_s |><|_~ p_t. This script builds those scopes and
runs HyFD-Partial over them.

Candidate joins are the cross-store INDs whose referenced attribute is unique; otherwise the join
fans out and produces FDs that are artifacts of multiplicity. An FD X -> Y over the join is
reported as inter-model only when Y is outside the closure of X under the join equality, the UCC
on the referenced side, and the FDs holding within each side.

The IND coverage (--ind-rho) decides which joins exist and the FD threshold (--fd) how partial an
FD over a join may be; they are deliberately separate. The default is an exact FD over a possibly
partial join, so the coverage an FD inherits is reported rather than absorbed.

usage: inter_model_fd.py [--ind-rho=1.0] [--fd=1.0] [exportDir] [logDir]
  requires MM_CP; run  source core/adapters/build.sh  first.
"""
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
RHO = flag("ind-rho", "1.0")
FD_THRESHOLD = flag("fd", "1.0")
EXPORT = Path(POSITIONAL[0]) if POSITIONAL else paths.DATASET / "metalevel"
LOGS = (Path(POSITIONAL[1]) if len(POSITIONAL) > 1
        else (paths.WORK_ROOT / f"compare/mm-{RHO}"))
OUT = (paths.WORK_ROOT / "compare/joins")

TRAILING_COMMA = re.compile(r",\s*([}\]])")
# Metanome prints an FD as  [rel.a, rel.b]->rel.c#degree  with one relation name throughout.
METANOME_FD = re.compile(r"^\[(.*)\]->(.*?)(?:#[\d.]+)?$")
MAX_ROWS = 400_000
# A join predicate over a handful of values is not a join: o_shippriority is constant in TPC-H and
# is included in every key column, so it would pair all 40,000 orders with one nation.
MIN_DISTINCT = 10


def store_of(relation):
    """Which data source an observation set belongs to; scopes of one store are not two stores."""
    base = relation.split("@")[0]
    if base.startswith("neo4j:"):
        return "graph"
    if base.startswith("shop.") or base.startswith("unibench."):
        return "document"
    return "relational"


def read_set(path):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def cross_store_inds():
    # run_all.sh nests SPIND's output under ind-results/; a plain SPIND run writes it directly.
    path = LOGS / "ind-results" / "1-ary_pINDs.json"
    if not path.exists():
        path = LOGS / "1-ary_pINDs.json"
    text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
    out = []
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            # Attribute names are kept whole. An array scope carries the full path from the entity
            # root, so lineitems.l_partkey is the column; truncating it to the leaf finds nothing.
            dependent = ".".join(pind["dependant"])
            for ref in pind["referenced"]:
                if store_of(relation["relation"]) == store_of(ref["relation"]):
                    continue
                out.append({
                    "dependent_set": relation["relation"], "dependent": dependent,
                    "referenced_set": ref["relation"],
                    "referenced": ".".join(ref["attribute"]),
                    "degree": ref.get("partialDegree", 1.0),
                })
    return out


# --------------------------------------------------------------------------------- running HyFD

def run_hyfd(path, threshold, classpath):
    dump = path.with_suffix(".fds")
    result = subprocess.run(
        ["java", "-Xmx8g", "-cp", classpath, "HyFdMultiModel", "file", str(path), "-",
         str(threshold), str(dump)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if dump.exists():
        return [line for line in dump.read_text(encoding="utf-8").splitlines() if line.strip()]
    sys.stderr.write(result.stdout[-2000:] + result.stderr[-2000:])
    return []


def parse_dump(lines, columns, rename=lambda c: c):
    """(determinant, dependent) pairs with Metanome's relation prefix removed.

    The prefix is inferred from the data rather than assumed: the relation name of a materialized
    join is built from two observation-set names and so contains column strings itself, which
    defeats any attempt to recognise a column by its shape.
    """
    by_length = sorted(columns, key=len, reverse=True)

    def prefix_of(token):
        for column in by_length:
            if token.endswith("." + column):
                return token[:-len(column)]
        return None

    prefix = None
    for line in lines:
        match = METANOME_FD.match(line.strip())
        if match:
            prefix = prefix_of(match.group(2).strip())
            if prefix:
                break
    if prefix is None:
        return []

    rules = []
    for line in lines:
        match = METANOME_FD.match(line.strip())
        if not match:
            continue
        tokens = [t.strip() for t in match.group(1).split(",") if t.strip()]
        if any(not t.startswith(prefix) for t in tokens):
            continue
        dependent = match.group(2).strip()
        if not dependent.startswith(prefix):
            continue
        rules.append((frozenset(rename(t[len(prefix):]) for t in tokens),
                      rename(dependent[len(prefix):])))
    return rules


# ------------------------------------------------------------------------------------- closure

def closure(attributes, rules):
    """Armstrong closure of a set of attributes under a set of FDs."""
    result = set(attributes)
    changed = True
    while changed:
        changed = False
        for determinant, dependent in rules:
            if dependent not in result and determinant <= result:
                result.add(dependent)
                changed = True
    return result


def classify(join_rules, base_rules, ind):
    """Splits the join scope's FDs by which sides they relate and whether they are entailed."""
    left_key, right_key = f"L_{ind['dependent']}", f"R_{ind['referenced']}"
    buckets = {"intra-left": [], "intra-right": [], "constant": [],
               "entailed": [], "inter-model": [], "mixed": []}
    # The join equality makes the two key columns interchangeable on every match, and the UCC on
    # the referenced side is already among the base rules, having been discovered on that side.
    rules = list(base_rules) + [(frozenset({left_key}), right_key),
                                (frozenset({right_key}), left_key)]
    for determinant, dependent in join_rules:
        readable = (", ".join(sorted(determinant)) or "(constant)") + " -> " + dependent
        sides = {c[0] for c in determinant | {dependent}}
        if not determinant:
            buckets["constant"].append(readable)
        elif sides == {"L"}:
            buckets["intra-left"].append(readable)
        elif sides == {"R"}:
            buckets["intra-right"].append(readable)
        elif dependent in closure(determinant, rules):
            buckets["entailed"].append(readable)
        elif len({c[0] for c in determinant}) > 1:
            # A determinant drawn from both sides is not a statement about one store determining
            # something in another. Over a wide join these are mostly coincidental minimal
            # determinants -- the ordinary combinatorics of FD discovery, not a cross-model fact.
            buckets["mixed"].append(readable)
        else:
            buckets["inter-model"].append(readable)
    return buckets


# ------------------------------------------------------------------------------- materialization

def materialize(ind, files, rows_cache):
    """Writes the join-induced scope, or explains why it is not well defined."""
    left, right = ind["dependent_set"], ind["referenced_set"]
    for name in (left, right):
        if name not in rows_cache:
            rows_cache[name] = read_set(files[name])
    left_columns, left_rows = rows_cache[left]
    right_columns, right_rows = rows_cache[right]

    key = ind["referenced"]
    if key not in right_columns or ind["dependent"] not in left_columns:
        return None, "join column missing from the export"

    distinct = len({row[ind["dependent"]] for row in left_rows if row.get(ind["dependent"])})
    if distinct < MIN_DISTINCT:
        return None, f"join predicate has {distinct} distinct values on the dependent side"

    index, duplicated = {}, False
    for row in right_rows:
        value = row.get(key, "")
        if value == "":
            continue
        if value in index:
            duplicated = True
            break
        index[value] = row
    if duplicated:
        return None, "referenced side is not unique (no UCC, join would fan out)"
    if not index:
        return None, "referenced side is empty"

    columns = [f"L_{c}" for c in left_columns] + [f"R_{c}" for c in right_columns]
    name = re.sub(r"[^A-Za-z0-9._-]", "_",
                  f"{left}--{ind['dependent']}--{right}".replace(":", "_").replace("@", "_"))
    path = OUT / f"{name}.csv"
    OUT.mkdir(parents=True, exist_ok=True)

    matched = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in left_rows:
            partner = index.get(row.get(ind["dependent"], ""))
            if partner is None:
                continue
            writer.writerow([row.get(c, "") for c in left_columns]
                            + [partner.get(c, "") for c in right_columns])
            matched += 1
            if matched >= MAX_ROWS:
                break
    if matched == 0:
        return None, "no matches"
    return {"path": path, "matched": matched, "left_rows": len(left_rows),
            "coverage": matched / len(left_rows), "columns": columns}, None


# ------------------------------------------------------------------------------------------ main

def side_rules(name, files, side, cache, classpath):
    """FDs holding within one observation set, with columns renamed to their side in the join."""
    if (name, side) not in cache:
        columns, _ = read_set(files[name])
        # A one-column observation set has no non-trivial FD, and HyFD's sampler throws
        # ArrayIndexOutOfBounds on one (every scalar array scope has one column).
        lines = run_hyfd(files[name], FD_THRESHOLD, classpath) if len(columns) > 1 else []
        cache[(name, side)] = parse_dump(lines, columns, rename=lambda c: f"{side}_{c}")
    return cache[(name, side)]


def main():
    classpath = os.environ.get("MM_CP", "")
    if not classpath:
        sys.exit("MM_CP is not set: run  source core/adapters/build.sh  first")

    entries = json.loads((EXPORT / "catalog.json").read_text(encoding="utf-8"))
    files = {entry["relation"]: EXPORT / entry["file"] for entry in entries}
    candidates = cross_store_inds()

    print(f"IND coverage rho = {RHO}, FD threshold = {FD_THRESHOLD}, export = {EXPORT}")
    print(f"{len(candidates)} cross-store INDs are candidate join predicates\n")

    rows_cache, fd_cache, findings, skipped = {}, {}, [], {}
    for ind in candidates:
        label = (f"{ind['dependent_set']}.{ind['dependent']} -> "
                 f"{ind['referenced_set']}.{ind['referenced']}")
        join, why = materialize(ind, files, rows_cache)
        if join is None:
            skipped.setdefault(why, []).append(label)
            continue

        base = (side_rules(ind["dependent_set"], files, "L", fd_cache, classpath)
                + side_rules(ind["referenced_set"], files, "R", fd_cache, classpath))
        join_rules = parse_dump(run_hyfd(join["path"], FD_THRESHOLD, classpath), join["columns"])
        buckets = classify(join_rules, base, ind)

        print(f"  join  {label}")
        print(f"          {join['matched']:,} of {join['left_rows']:,} matches, "
              f"coverage {join['coverage']:.4f}, {len(join_rules)} FDs over the joined scope")
        print(f"          {len(buckets['intra-left'])} intra-left, "
              f"{len(buckets['intra-right'])} intra-right, {len(buckets['constant'])} constant, "
              f"{len(buckets['entailed'])} entailed, {len(buckets['mixed'])} mixed determinant, "
              f"{len(buckets['inter-model'])} inter-model")
        for fd in buckets["inter-model"]:
            print(f"            {fd}")
        findings.append((label, join, buckets))

    for why, labels in sorted(skipped.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  {len(labels)} skipped: {why}")
        for label in labels[:4]:
            print(f"      {label}")
        if len(labels) > 4:
            print(f"      ... and {len(labels) - 4} more")

    entailed = sum(len(b["entailed"]) for _, _, b in findings)
    mixed = sum(len(b["mixed"]) for _, _, b in findings)
    novel = sum(len(b["inter-model"]) for _, _, b in findings)
    print(f"\n  {len(findings)} join-induced scopes built")
    print(f"  {entailed} cross-side FDs entailed by the join equality, the UCC and the "
          f"single-scope FDs")
    print(f"  {mixed} with a determinant drawn from both sides (not a cross-store statement)")
    print(f"  {novel} inter-model FDs: one side determines the other, not entailed")
    if findings:
        worst = min(join["coverage"] for _, join, _ in findings)
        print(f"  lowest coverage inherited from the underlying IND: {worst:.4f}")


if __name__ == "__main__":
    main()
