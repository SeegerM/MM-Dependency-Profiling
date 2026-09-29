"""Checks every partial FD and UCC against the data it was discovered on.

A partial FD X -> Y at threshold theta is meant to say that at most (1 - theta) * |rows| records
must be removed for the dependency to hold exactly. That quantity is the standard g3 error, and it
is computable directly: group the rows by X, and in each group keep only the most frequent Y.

This is worth checking rather than assuming, because a partial-FD result is only interpretable if
the number attached to it means what the definition says. Any FD reported at theta whose g3 error
exceeds the budget is a false positive of the implementation, not a property of the data.

For a UCC the same reading applies: a column combination K is a partial key at theta when at most
(1 - theta) * |rows| records must be removed for K to be unique, which is |rows| minus the number
of distinct K-values.

usage: verify_partial_fds.py <exportDir> <dumpDir> [--thresholds=1.0,0.75,0.5]
  dumpDir holds <relation>-<theta>.fds and .uccs files as written by relaxation_sweep.py.
"""
import csv
import re
import sys
from pathlib import Path

FD = re.compile(r"^\[(.*)\]->(.*?)(?:#([\d.]+))?$")
UCC = re.compile(r"^\[(.*)\](?:#([\d.]+))?$")


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
EXPORT, DUMPS = Path(POSITIONAL[0]), Path(POSITIONAL[1])
THRESHOLDS = flag("thresholds", "1.0,0.95,0.75,0.5").split(",")


def strip(token, columns):
    for column in sorted(columns, key=len, reverse=True):
        if token.endswith("." + column):
            return column
    return None


def g3(rows, determinant, dependent):
    """Minimum number of records to delete for the FD to hold exactly."""
    groups = {}
    for row in rows:
        groups.setdefault(tuple(row[c] for c in determinant), []).append(row[dependent])
    return sum(len(v) - max(v.count(x) for x in set(v)) for v in groups.values())


def uccs_report():
    """The same check for UCCs: K is a partial key at theta when at most (1 - theta) * |rows|
    records must be removed for it to be unique, i.e. |rows| minus the distinct K-value count."""
    print("\n  partial UCCs\n")
    print(f"  {'threshold':>10} {'reported':>9} {'within budget':>14} {'violating':>10}"
          f" {'unary':>7} {'composite':>10}")
    for threshold in THRESHOLDS:
        reported = ok = bad = bad_unary = bad_composite = 0
        for path in sorted(DUMPS.glob(f"*-{threshold}.uccs")):
            relation = path.name[: -len(f"-{threshold}.uccs")]
            source = EXPORT / f"{relation}.csv"
            if not source.exists():
                continue
            with source.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                columns = reader.fieldnames or []
                rows = list(reader)
            budget = len(rows) - len(rows) * float(threshold)
            for line in path.read_text(encoding="utf-8").splitlines():
                match = UCC.match(line.strip())
                if not match:
                    continue
                key = [strip(t.strip(), columns) for t in match.group(1).split(",") if t.strip()]
                if not key or None in key:
                    continue
                reported += 1
                error = len(rows) - len({tuple(row[c] for c in key) for row in rows})
                if error <= budget:
                    ok += 1
                else:
                    bad += 1
                    if len(key) <= 1:
                        bad_unary += 1
                    else:
                        bad_composite += 1
        print(f"  {threshold:>10} {reported:>9} {ok:>14} {bad:>10} {bad_unary:>7}"
              f" {bad_composite:>10}")


def main():
    print(f"export {EXPORT}\ndumps  {DUMPS}\n")
    print("  partial FDs\n")
    print(f"  {'threshold':>10} {'reported':>9} {'within budget':>14} {'violating':>10}"
          f" {'unary det.':>11} {'composite det.':>15}")

    worst = []
    for threshold in THRESHOLDS:
        reported = ok = bad = bad_unary = bad_composite = 0
        for path in sorted(DUMPS.glob(f"*-{threshold}.fds")):
            relation = path.name[: -len(f"-{threshold}.fds")]
            source = EXPORT / f"{relation}.csv"
            if not source.exists():
                continue
            with source.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                columns = reader.fieldnames or []
                rows = list(reader)
            budget = len(rows) - len(rows) * float(threshold)

            for line in path.read_text(encoding="utf-8").splitlines():
                match = FD.match(line.strip())
                if not match:
                    continue
                determinant = [strip(t.strip(), columns) for t in match.group(1).split(",")
                               if t.strip()]
                dependent = strip(match.group(2).strip(), columns)
                if dependent is None or None in determinant:
                    continue
                reported += 1
                error = g3(rows, determinant, dependent)
                if error <= budget:
                    ok += 1
                else:
                    bad += 1
                    if len(determinant) <= 1:
                        bad_unary += 1
                    else:
                        bad_composite += 1
                    worst.append((error - budget, threshold, relation,
                                  ", ".join(determinant) or "(empty)", dependent, error,
                                  int(budget), match.group(3)))
        print(f"  {threshold:>10} {reported:>9} {ok:>14} {bad:>10} {bad_unary:>11}"
              f" {bad_composite:>15}")

    if worst:
        print("\n  worst offenders (g3 error against the budget the threshold allows):")
        for _, threshold, relation, determinant, dependent, error, budget, degree in \
                sorted(worst, reverse=True)[:8]:
            print(f"    theta={threshold:<5} {relation:<24} {determinant} -> {dependent}")
            print(f"      needs {error:,} deletions, budget {budget:,}, "
                  f"reported degree {degree}")
    uccs_report()


if __name__ == "__main__":
    main()
