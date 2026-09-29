"""Applies the chance correction of profile/genuineness.py to FDs and UCCs.

Inclusions are scored by `profile/genuineness.py` from column statistics; FDs and UCCs need the
observation set itself, since both quantify over pairs of records.

  FDs   pdep(X -> Y) rises with |X| regardless of the data. gpdep subtracts CPFD's expectation, so
        an FD with gpdep near zero holds only because of its determinant's cardinality.

  UCCs  under independence, n records are expected to contain C(n, 2) * prod_i pdep(A_i) duplicate
        pairs; observing none is informative only when that number is large. For a unary UCC the
        instance-wide background distribution supplies the expectation instead.

The FD and UCC dumps come from the same adapters run_all.sh uses and are reused when present.

usage: genuineness_sweep.py <exportDir> [--threshold=1.0] [--top=15] [--rerun]
"""
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "profile"))
from genuineness import Background, gpdep, pdep  # noqa: E402

import csv  # noqa: E402

METANOME = re.compile(r"^\[(.*)\]\s*->\s*(.*?)(?:#([0-9.]+))?$")
METANOME_UCC = re.compile(r"^\[(.*)\]\s*(?:#([0-9.]+))?$")


def flag(name, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


# ------------------------------------------------------------------------------ reading the dumps

def strip_prefix(token, columns):
    """Metanome qualifies every column with the relation name; the relation name is not fixed.

    A materialized join is named after two observation sets and so contains column strings itself,
    which defeats recognising a column by its shape. The longest matching suffix is used instead.
    """
    for column in sorted(columns, key=len, reverse=True):
        if token == column or token.endswith("." + column):
            return column
    return token.strip()


def read_fds(path, columns):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = METANOME.match(line.strip())
        if not match:
            continue
        determinant = [strip_prefix(t, columns) for t in match.group(1).split(",") if t.strip()]
        out.append((tuple(determinant), strip_prefix(match.group(2), columns),
                    float(match.group(3) or 1.0)))
    return out


def read_uccs(path, columns):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = METANOME_UCC.match(line.strip())
        if not match:
            continue
        out.append(tuple(strip_prefix(t, columns) for t in match.group(1).split(",") if t.strip()))
    return out


def ensure_dump(path, suffix, tool, threshold, classpath, rerun):
    dump = path.with_suffix(suffix)
    if dump.exists() and not rerun:
        return dump
    subprocess.run(["java", "-Xmx8g", "-cp", classpath, tool, "file", str(path), "-",
                    str(threshold), str(dump)],
                   capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return dump if dump.exists() else None


# ----------------------------------------------------------------------------------- the measures

def score_fds(rows, fds, total):
    """(fd, pdep, E[pdep], gpdep) for every discovered FD, most genuine first."""
    scored = []
    for determinant, dependent, degree in fds:
        groups = defaultdict(Counter)
        dependent_counts = Counter()
        for row in rows:
            key = tuple(row.get(c, "") for c in determinant)
            value = row.get(dependent, "")
            groups[key][value] += 1
            dependent_counts[value] += 1
        sizes = [sum(inner.values()) for inner in groups.values()]
        inner = [list(counter.values()) for counter in groups.values()]
        observed, expected, genuine = gpdep(sizes, inner, list(dependent_counts.values()), total)
        scored.append({"determinant": list(determinant), "dependent": dependent, "degree": degree,
                       "groups": len(groups), "pdep": observed, "expected": expected,
                       "gpdep": genuine})
    scored.sort(key=lambda s: -s["gpdep"])
    return scored


def score_uccs(rows, uccs, total, background, relation, columns):
    """(ucc, expected duplicate pairs, log10 of them) for every discovered UCC.

    The expectation is the number of duplicate pairs the members' own value distributions predict.
    Observing a key where the expectation is below one says nothing: the combination could not have
    collided anyway.
    """
    marginal = {}
    for column in columns:
        counts = Counter(row.get(column, "") for row in rows)
        marginal[column] = pdep(list(counts.values()), total)

    scored = []
    pairs = total * (total - 1) / 2
    for ucc in uccs:
        if len(ucc) >= 2:
            collision = 1.0
            for column in ucc:
                collision *= marginal[column]
            source = "marginals"
        else:
            # A unary UCC's own distribution is the observation, so it cannot also be the null. The
            # instance-wide distribution of its values supplies one that is not circular: a column
            # is surprisingly unique when the values it holds are common ones elsewhere.
            column = ucc[0]
            distinct = len(set(row.get(column, "") for row in rows if row.get(column)))
            share = background.share(f"{relation}.{column}", exclude=f"{relation}.{column}")
            collision = share / max(distinct, 1)
            source = "background"
        expected = pairs * collision
        scored.append({"ucc": list(ucc), "arity": len(ucc), "expected_collisions": expected,
                       "score": math.log10(expected) if expected > 0 else -math.inf,
                       "null": source})
    scored.sort(key=lambda s: -s["score"])
    return scored


# ------------------------------------------------------------------------------------------- main

def main():
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    export = Path(positional[0])
    threshold = flag("threshold", "1.0")
    top = int(flag("top", 15))
    rerun = "--rerun" in sys.argv[1:]
    classpath = os.environ.get("MM_CP", "")

    csv.field_size_limit(10_000_000)
    catalog_file = export / "catalog.json"
    if catalog_file.exists():
        catalog = json.loads(catalog_file.read_text(encoding="utf-8"))
    else:
        # A directory of materialized join-induced scopes has no catalog; its plain CSVs are
        # accepted as observation sets.
        catalog = [{"relation": path.stem, "file": path.name}
                   for path in sorted(export.glob("*.csv"))]

    # The background is the whole instance, so it is built before any one observation set is scored.
    background = Background(200_000)
    for entry in catalog:
        with (export / entry["file"]).open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                for column, value in row.items():
                    if value:
                        background.observe(f"{entry['relation']}.{column}", value)
    background.finish()

    all_fds, all_uccs = [], []
    for entry in catalog:
        path = export / entry["file"]
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            rows = list(reader)
        if not rows:
            continue
        fd_dump = (None if "--uccs-only" in sys.argv[1:] else
                   ensure_dump(path, ".fds", "HyFdMultiModel", threshold, classpath, rerun))
        ucc_dump = (None if "--fds-only" in sys.argv[1:] else
                    ensure_dump(path, ".uccs", "HyUccMultiModel", threshold, classpath, rerun))
        if fd_dump:
            for scored in score_fds(rows, read_fds(fd_dump, columns), len(rows)):
                all_fds.append({**scored, "relation": entry["relation"]})
        if ucc_dump:
            for scored in score_uccs(rows, read_uccs(ucc_dump, columns), len(rows), background,
                                     entry["relation"], columns):
                all_uccs.append({**scored, "relation": entry["relation"]})

    print(f"{export.parent.name}: {len(all_fds)} FDs, {len(all_uccs)} UCCs at threshold {threshold}\n")

    # Floating point: a dependency fully explained by its determinant reaches gpdep of
    # plus or minus a few ulps, not exactly zero, and the sign of that noise is arbitrary.
    accidental = sum(1 for f in all_fds if f["gpdep"] <= 1e-9)
    print(f"  FDs   {accidental} of {len(all_fds)} have gpdep <= 0: the determinant's cardinality "
          f"accounts for the whole dependency")
    print(f"  {'gpdep':>8} {'pdep':>8} {'E[pdep]':>9} {'groups':>7}  dependency")
    for entry in all_fds[:top]:
        print(f"  {entry['gpdep']:>8.4f} {entry['pdep']:>8.4f} {entry['expected']:>9.4f} "
              f"{entry['groups']:>7}  {entry['relation']}.{','.join(entry['determinant'])}"
              f" -> {entry['dependent']}")
    if len(all_fds) > top:
        print(f"  ... and {len(all_fds) - top} more")
        for entry in all_fds[-3:]:
            print(f"  {entry['gpdep']:>8.4f} {entry['pdep']:>8.4f} {entry['expected']:>9.4f} "
                  f"{entry['groups']:>7}  {entry['relation']}.{','.join(entry['determinant'])}"
                  f" -> {entry['dependent']}")

    unsurprising = sum(1 for u in all_uccs if u["expected_collisions"] < 1.0)
    print(f"\n  UCCs  {unsurprising} of {len(all_uccs)} would have been unique by accident: fewer "
          f"than one duplicate pair was expected of them in the first place")
    print(f"  {'log10 E':>8} {'arity':>6} {'null':>11}  key")
    for entry in all_uccs[:top]:
        print(f"  {entry['score']:>8.2f} {entry['arity']:>6} {entry['null']:>11}  "
              f"{entry['relation']}.[{','.join(entry['ucc'])}]")
    if len(all_uccs) > top:
        print(f"  ... and {len(all_uccs) - top} more")

    out = flag("json")
    if out:
        Path(out).write_text(json.dumps({"fds": all_fds, "uccs": all_uccs}, indent=2),
                             encoding="utf-8")
        print(f"\n  written to {out}")


if __name__ == "__main__":
    main()
