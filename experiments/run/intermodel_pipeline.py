"""Discovers inter-model FDs end to end, applying the same stages to every instance.

Combines inter_model_fd.py (join-induced scopes, entailment), genuineness_sweep.py (gpdep) and
profile/genuineness.py into one run that prints the count after every stage:

  1  candidate predicates   cross-store INDs.
  2a qualification          the predicate must be a reference:
                              (a) an equality IND (a UCC bounds the fan-out of an equality join
                                  but not of a similarity join);
                              (b) the referenced side is unique and null-free over its scope;
                              (c) the dependent is non-empty.
  2b utilisation filter     util = |supp(L) & supp(R)| / |supp(R)| >= u. Not a condition for
                            being a reference: it drops subset artifacts, where a predicate that
                            reaches only a few referenced records makes their attributes trivially
                            constant within each left group.
  3  materialization        the join-induced scope, one CSV per qualified predicate.
  4  FD discovery           HyFD over that scope at the given threshold (or, with --unary,
                            single-attribute determinants only).
  5  novelty                drop FDs entailed by the join equality, the referenced UCC and the
                            single-side FDs, and FDs whose determinant draws on both sides.
  6  genuineness            drop FDs with gpdep <= 0.

The pipeline runs once at u = 0; the u filter and --sweep are applied to its findings afterwards.

usage: intermodel_pipeline.py <exportDir> <indResultDir> [--fd=1.0] [--u=0.1] [--sweep]
                              [--unary]
                              [--out=<dir>] [--json=<file>]
  requires MM_CP; run  source core/adapters/build.sh  first.
"""
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "profile"))
sys.path.insert(0, str(HERE.parent))
from genuineness import gpdep  # noqa: E402
# Classification and FD parsing are shared with inter_model_fd.py so the entailment rules exist once.
from inter_model_fd import classify, parse_dump, run_hyfd, side_rules, store_of  # noqa: E402

TRAILING_COMMA = re.compile(r",\s*([}\]])")
MAX_ROWS = 400_000


def flag(name, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


# ------------------------------------------------------------------------------ stage 1: inputs

def read_export(export):
    """Per observation set: columns, per-column support, non-null counts, row count."""
    csv.field_size_limit(10_000_000)
    sets = {}
    for entry in json.loads((export / "catalog.json").read_text(encoding="utf-8")):
        path = export / entry["file"]
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            support = {c: set() for c in columns}
            filled = dict.fromkeys(columns, 0)
            rows = 0
            for row in reader:
                rows += 1
                for column in columns:
                    if row.get(column):
                        support[column].add(row[column])
                        filled[column] += 1
        sets[entry["relation"]] = {"columns": columns, "support": support, "filled": filled,
                                   "rows": rows, "path": path}
    return sets


def cross_store_inds(results):
    """Stage 1: every discovered inclusion whose two sides live in different sources."""
    out = []
    for path in sorted(Path(results).glob("*ary_pINDs.json")):
        text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
        for relation in json.loads(text):
            for pind in relation.get("pINDs", []):
                dependent = ".".join(pind["dependant"])
                for ref in pind["referenced"]:
                    if store_of(relation["relation"]) == store_of(ref["relation"]):
                        continue
                    out.append({"dependent_set": relation["relation"], "dependent": dependent,
                                "referenced_set": ref["relation"],
                                "referenced": ".".join(ref["attribute"]),
                                "degree": ref.get("partialDegree", 1.0)})
    return out


# ----------------------------------------------------------------------- stage 2: qualification

def qualify(ind, sets):
    """The four conditions, evaluated in order. Returns (utilisation, None) or (None, reason)."""
    left, right = sets.get(ind["dependent_set"]), sets.get(ind["referenced_set"])
    if not left or not right:
        return None, "observation set missing from the export"
    if ind["dependent"] not in left["support"] or ind["referenced"] not in right["support"]:
        return None, "attribute missing from the export"

    if ind["degree"] < 1.0:
        return None, "(a) not an equality inclusion"

    referenced = right["support"][ind["referenced"]]
    if not (right["rows"] and len(referenced) == right["rows"]
            and right["filled"][ind["referenced"]] == right["rows"]):
        return None, "(b) referenced side is not a candidate key"

    dependent = left["support"][ind["dependent"]]
    if not dependent:
        return None, "(c) dependent observes nothing"

    return len(dependent & referenced) / len(referenced), None


# --------------------------------------------------------------------- stage 3: materialization

def materialize(ind, sets, out_dir):
    """The join-induced scope as a CSV, with both sides' columns prefixed by their side."""
    left, right = sets[ind["dependent_set"]], sets[ind["referenced_set"]]
    index = {}
    with right["path"].open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            value = row.get(ind["referenced"], "")
            if value:
                index[value] = row
    if not index:
        return None

    columns = [f"L_{c}" for c in left["columns"]] + [f"R_{c}" for c in right["columns"]]
    name = re.sub(r"[^A-Za-z0-9._-]", "_",
                  f"{ind['dependent_set']}--{ind['dependent']}--{ind['referenced_set']}"
                  .replace(":", "_").replace("@", "_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.csv"

    matched = 0
    source_rows = 0
    defined_in_scan = 0
    with left["path"].open(encoding="utf-8", newline="") as source, \
            path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in csv.DictReader(source):
            source_rows += 1
            value = row.get(ind["dependent"], "")
            if value:
                defined_in_scan += 1
            partner = index.get(value)
            if partner is None:
                continue
            writer.writerow([row.get(c, "") for c in left["columns"]]
                            + [partner.get(c, "") for c in right["columns"]])
            matched += 1
            if matched >= MAX_ROWS:
                break
    if matched == 0:
        return None
    # participation = presence x coverage_bag: a join shrinks because the extractor is absent or
    # because the reference dangles. All three are computed over the same scanned prefix, since
    # the scan may stop at MAX_ROWS.
    return {"path": path, "matched": matched, "source_matches": source_rows,
            "columns": columns,
            "presence": defined_in_scan / source_rows if source_rows else 0.0,
            "coverage_bag": matched / defined_in_scan if defined_in_scan else 0.0,
            "participation": matched / source_rows if source_rows else 0.0}


# ------------------------------------------------------------------- stage 4, unary-determinant

def unary_rules(path, rename=lambda c: c):
    """Every FD with a one-attribute determinant, read straight off the scope.

    X -> Y holds iff Y is constant within each of X's value groups; checking all ordered pairs is
    O(rows x columns^2), avoiding HyFD's search over the determinant lattice.
    """
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = [row + [""] * (len(header) - len(row)) for row in reader]
    if not rows:
        return []
    width = len(header)
    rules = []
    for i in range(width):
        groups = defaultdict(list)
        for row in rows:
            groups[row[i]].append(row)
        violated = set()
        for members in groups.values():
            if len(members) < 2:
                continue
            first = members[0]
            for other in members[1:]:
                for j in range(width):
                    if j not in violated and other[j] != first[j]:
                        violated.add(j)
                if len(violated) == width:
                    break
            if len(violated) == width:
                break
        for j in range(width):
            if j != i and j not in violated:
                rules.append((frozenset({rename(header[i])}), rename(header[j])))
    return rules


def unary_side_rules(name, files, side, cache):
    """The same, for one side of the join, with the side prefix the classifier expects."""
    if (name, side) not in cache:
        cache[(name, side)] = unary_rules(files[name], rename=lambda c: f"{side}_{c}")
    return cache[(name, side)]


# ------------------------------------------------------------------------- stage 6: genuineness

def gpdep_of(rows, determinant, dependent, total):
    """gpdep for one FD over the materialized scope."""
    groups = defaultdict(Counter)
    dependent_counts = Counter()
    for row in rows:
        key = tuple(row.get(c, "") for c in sorted(determinant))
        value = row.get(dependent, "")
        groups[key][value] += 1
        dependent_counts[value] += 1
    sizes = [sum(inner.values()) for inner in groups.values()]
    inner = [list(counter.values()) for counter in groups.values()]
    return gpdep(sizes, inner, list(dependent_counts.values()), total)[2]


# ------------------------------------------------------------------------------------- pipeline

def run(export, results, threshold, out_dir, classpath, unary=False):
    """Every stage, at u = 0. Each surviving FD carries the utilisation of its predicate."""
    sets = read_export(export)
    files = {name: data["path"] for name, data in sets.items()}
    candidates = cross_store_inds(results)

    counts = {"candidates": len(candidates), "qualified": 0, "scopes": 0, "cross_side": 0,
              "entailed": 0, "mixed": 0, "non_entailed": 0, "accidental": 0, "inter_model": 0}
    rejected, findings, scopes = Counter(), [], []
    fd_cache = {}

    for ind in candidates:
        utilisation, reason = qualify(ind, sets)
        if reason:
            rejected[reason] += 1
            continue
        counts["qualified"] += 1

        join = materialize(ind, sets, out_dir)
        if join is None:
            rejected["(e) join produced no matches"] += 1
            continue
        counts["scopes"] += 1

        if unary:
            base = (unary_side_rules(ind["dependent_set"], files, "L", fd_cache)
                    + unary_side_rules(ind["referenced_set"], files, "R", fd_cache))
            rules = unary_rules(join["path"])
        else:
            base = (side_rules(ind["dependent_set"], files, "L", fd_cache, classpath)
                    + side_rules(ind["referenced_set"], files, "R", fd_cache, classpath))
            rules = parse_dump(run_hyfd(join["path"], threshold, classpath), join["columns"])
        buckets = classify(rules, base, ind)

        counts["cross_side"] += (len(buckets["entailed"]) + len(buckets["mixed"])
                                 + len(buckets["inter-model"]))
        counts["entailed"] += len(buckets["entailed"])
        counts["mixed"] += len(buckets["mixed"])
        counts["non_entailed"] += len(buckets["inter-model"])

        label = (f"{ind['dependent_set']}.{ind['dependent']} -> "
                 f"{ind['referenced_set']}.{ind['referenced']}")
        scopes.append({"predicate": label, "utilisation": utilisation, **
                       {k: v for k, v in join.items() if k != "path" and k != "columns"}})

        if not buckets["inter-model"]:
            continue
        with join["path"].open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for readable in buckets["inter-model"]:
            determinant_text, dependent = readable.split(" -> ")
            determinant = frozenset(t.strip() for t in determinant_text.split(",") if t.strip())
            score = gpdep_of(rows, determinant, dependent, len(rows))
            if score <= 1e-9:
                counts["accidental"] += 1
                continue
            counts["inter_model"] += 1
            findings.append({"predicate": label, "utilisation": utilisation, "fd": readable,
                             "gpdep": score, "participation": join["participation"],
                             "presence": join["presence"],
                             "coverage_bag": join["coverage_bag"]})
    return counts, rejected, findings, scopes


def report(name, counts, rejected, findings, scopes, floor):
    surviving = [f for f in findings if f["utilisation"] >= floor]
    print(f"{name}: the six stages, at u = {floor}\n")
    rows = [("1  cross-store inclusions", counts["candidates"]),
            ("2a qualified as references", counts["qualified"]),
            ("2b of those, utilisation >= u",
             sum(1 for s in scopes if s["utilisation"] >= floor)),
            ("3  join-induced scopes built", counts["scopes"]),
            ("4  cross-side FDs discovered", counts["cross_side"]),
            ("5  entailed", counts["entailed"]),
            ("   determinant from both sides",
             "--" if counts.get("unary") else counts["mixed"]),
            ("   non-entailed, one side determining", counts["non_entailed"]),
            ("6  of those, gpdep <= 0", counts["accidental"]),
            ("   inter-model FDs at u = 0", counts["inter_model"]),
            ("   inter-model FDs at u", len(surviving))]
    for label, value in rows:
        print(f"  {label:<40} {value:>8}" if isinstance(value, str)
              else f"  {label:<40} {value:>8,}")

    if rejected:
        print("\n  predicates rejected at stage 2a, as not references:")
        for reason, count in sorted(rejected.items()):
            print(f"    {reason:<48} {count:>6,}")

    if surviving:
        print(f"\n  the {len(surviving)} inter-model FDs:")
        for entry in sorted(surviving, key=lambda f: -f["gpdep"]):
            print(f"    gpdep {entry['gpdep']:.4f}  part {entry['participation']:.4f}"
                  f"  = pres {entry['presence']:.4f} x cov_bag {entry['coverage_bag']:.4f}"
                  f"   {entry['fd']}")
            print(f"      over {entry['predicate']}  (utilisation {entry['utilisation']:.4f})")

    dropped = [f for f in findings if f["utilisation"] < floor]
    if dropped:
        print(f"\n  {len(dropped)} dropped at stage 2b by the utilisation filter. These predicates"
              f"\n  are references; what disqualifies them is that the scopes they induce are"
              f"\n  subsets of the referenced entity set:")
        for entry in sorted(dropped, key=lambda f: -f["utilisation"]):
            print(f"    util {entry['utilisation']:.4f}  gpdep {entry['gpdep']:.4f}   "
                  f"{entry['fd']}  over {entry['predicate']}")


def sweep(findings):
    """The count at every u, so the reader can see whether the choice of u decides anything."""
    print("\n  sensitivity of the result to u:")
    print(f"    {'u':>8} {'inter-model FDs':>17}")
    boundaries = sorted({0.0} | {f["utilisation"] for f in findings} | {1.0})
    for value in boundaries:
        print(f"    {value:>8.4f} {sum(1 for f in findings if f['utilisation'] >= value):>17}")


def main():
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    export, results = Path(positional[0]), Path(positional[1])
    threshold = flag("fd", "1.0")
    floor = float(flag("u", 0.1))
    out_dir = Path(flag("out", str(Path(results).parent / "pipeline-joins")))
    classpath = os.environ.get("MM_CP", "")
    if not classpath:
        sys.exit("MM_CP is not set: run  source core/adapters/build.sh  first")

    unary = "--unary" in sys.argv[1:]
    counts, rejected, findings, scopes = run(export, results, threshold, out_dir, classpath,
                                             unary=unary)
    counts["unary"] = unary
    report(export.parent.name, counts, rejected, findings, scopes, floor)
    if "--sweep" in sys.argv[1:]:
        sweep(findings)

    out = flag("json")
    if out:
        Path(out).write_text(json.dumps(
            {"counts": counts, "rejected": dict(rejected), "findings": findings,
             "scopes": scopes, "u": floor}, indent=2), encoding="utf-8")
        print(f"\n  written to {out}")


if __name__ == "__main__":
    main()
