"""Recomputes every unary inclusion directly from the definition and diffs it against what a run reported.

Recomputing degrees of returned candidates cannot detect a candidate the run wrongly pruned, so
this computes the result set independently. For every ordered pair of extractors it evaluates

    cov_bag(s, t) = sum over x in supp(s) inter supp(t) of mult_s(x), divided by |s|

and states the pair if that reaches rho, with no pruning or candidate generation.

Memory is bounded: referenced sides are held as sets, and the dependent's multiplicities as a
single Counter that is discarded before the next dependent is read.

usage: direct_ind_check.py [--work=$WORK_ROOT] [--run=<label substring>]
                           [--out=paper/tables/direct-ind-check.txt] [--emit=<dir>]
"""
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402

HERE = Path(__file__).resolve().parents[2]
TRAILING_COMMA = re.compile(r",\s*([}\]])")
csv.field_size_limit(1 << 24)

# (label, export, results, rho).
RUNS = [
    ("UniBench", "unibench/metalevel", "unibench/meta-results-0.5", 0.5),
    ("MovieLens", "movielens/metalevel", "movielens/meta-results-0.5", 0.5),
    ("Helsinki Patent", "hpatent/metalevel", "hpatent/meta-results-0.5", 0.5),
    ("ListenBrainz", "listenbrainz/metalevel", "listenbrainz/meta-results-0.5", 0.5),
    ("TPC-H", "mm_tpch/metalevel", "compare/mm-0.95-current/ind-results", 0.95),
    ("TPC-H dirty", "dirty/metalevel", "dirty/eq-0.99", 0.99),
    ("TPC-H dirty", "dirty/metalevel", "dirty/eq-0.95", 0.95),
    ("IMDB (full)", "imdb-full/metalevel", "imdb-full/eq-0.999-n1", 0.999),
    # Exact rather than partial; checks that the union-scope constructor preserves inclusions.
    ("MMSBench-Net", "coms/metalevel", "coms/meta-results-1.0", 1.0),
    ("Helsinki Person", "hperson/metalevel", "hperson/meta-results-0.5/ind-results", 0.5),
    ("Helsinki Film", "hfilm/metalevel", "hfilm/meta-results-0.5/ind-results", 0.5),
    ("Helsinki Univ.", "huniversity/metalevel",
     "huniversity/meta-results-0.5/ind-results", 0.5),
]


def flag(name, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def supports(export):
    """Every extractor's distinct values and non-null count. Sets only: no multiplicities yet."""
    catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    values, filled, where = {}, {}, {}
    for entry in catalog:
        path = export / entry["file"]
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            seen = [set() for _ in header]
            counts = [0] * len(header)
            for row in reader:
                for i, value in enumerate(row):
                    if i < len(header) and value != "":
                        seen[i].add(value)
                        counts[i] += 1
        for i, column in enumerate(header):
            name = entry["relation"] + "." + column
            values[name] = seen[i]
            filled[name] = counts[i]
            where[name] = (path, i)
    return values, filled, where


def multiplicities(path, index):
    """The value multiset of one column, read on demand and discarded by the caller."""
    counts = Counter()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            if index < len(row) and row[index] != "":
                counts[row[index]] += 1
    return counts


def reported(results):
    """The run's own inclusions, with the degree it reported for each."""
    text = TRAILING_COMMA.sub(r"\1", (results / "1-ary_pINDs.json").read_text(encoding="utf-8"))
    found = {}
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = relation["relation"] + "." + ".".join(pind["dependant"])
            for ref in pind["referenced"]:
                name = ref["relation"] + "." + ".".join(ref["attribute"])
                found[(dependent, name)] = ref.get("partialDegree", 1.0)
    return found


def check(export, results, rho):
    """The direct result set, and how the run differs from it."""
    values, filled, where = supports(export)
    run = reported(results)
    names = sorted(values)

    direct = {}
    for dependent in names:
        if not filled[dependent]:
            continue
        counts = multiplicities(*where[dependent])
        total = sum(counts.values())
        if not total:
            continue
        for referenced in names:
            if dependent == referenced:
                continue
            target = values[referenced]
            if not target:
                continue
            inside = sum(count for value, count in counts.items() if value in target)
            degree = inside / total
            if degree >= rho:
                direct[(dependent, referenced)] = degree
        del counts

    missing = sorted(set(direct) - set(run))
    # An inclusion whose dependent observes nothing is vacuously true; the run states it and the
    # direct computation does not, so these are counted apart from real disagreements.
    vacuous = sorted(p for p in set(run) - set(direct) if not filled.get(p[0]))
    extra = sorted(p for p in set(run) - set(direct) if filled.get(p[0]))
    return direct, run, missing, extra, vacuous


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    out = Path(flag("out", str(HERE / "paper" / "tables" / "direct-ind-check.txt")))
    only = flag("run")
    emit = Path(flag("emit")) if flag("emit") else None

    lines = [
        "# Every unary inclusion recomputed from the definition, against what each run reported.",
        "# Generated by experiments/run/direct_ind_check.py.",
        "#",
        "# Recomputing the degrees of the candidates a run returned cannot detect a candidate the run",
        "# pruned. The direct computation has no pruning, so 'missing' counts false negatives.",
        "",
        "  %-16s %7s %9s %9s %8s %7s %8s"
        % ("run", "rho", "direct", "reported", "missing", "extra", "vacuous"),
    ]
    detail = []
    totals = [0, 0, 0, 0]
    for label, export_rel, results_rel, rho in RUNS:
        name = "%s rho=%g" % (label, rho)
        if only and only.lower() not in name.lower():
            continue
        export, results = work / export_rel, work / results_rel
        if not export.exists() or not (results / "1-ary_pINDs.json").exists():
            lines.append("  %-16s %7g  not present" % (label, rho))
            continue
        direct, run, missing, extra, vacuous = check(export, results, rho)
        lines.append("  %-16s %7g %9d %9d %8d %7d %8d"
                     % (label, rho, len(direct), len(run) - len(vacuous),
                        len(missing), len(extra), len(vacuous)))
        totals[0] += len(direct)
        totals[1] += len(run) - len(vacuous)
        totals[2] += len(missing)
        totals[3] += len(extra)
        for pair in missing[:25]:
            detail.append((name, "missing", pair, direct[pair]))
        for pair in extra[:25]:
            detail.append((name, "extra", pair, run[pair]))
        if emit:
            emit.mkdir(parents=True, exist_ok=True)
            target = emit / (results_rel.replace("/", "_") + ".direct.json")
            target.write_text(json.dumps(
                [{"dependent": d, "referenced": r, "degree": g}
                 for (d, r), g in sorted(direct.items())], indent=1), encoding="utf-8")

    lines.append("  %-16s %7s %9d %9d %8d %7d"
                 % ("total", "", totals[0], totals[1], totals[2], totals[3]))

    if detail:
        lines += [
            "",
            "## disagreements",
            "  %-22s %-8s %-38s %-30s %8s"
            % ("run", "kind", "dependent", "referenced", "cov"),
        ]
        for name, kind, (dependent, referenced), value in detail:
            lines.append("  %-22s %-8s %-38s %-30s %8.4f"
                         % (name, kind, dependent[-38:], referenced[-30:], value))
    else:
        lines += ["", "## disagreements", "  none: every run agrees with the definition."]

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("written to " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
