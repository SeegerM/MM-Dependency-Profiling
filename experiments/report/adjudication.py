"""Stratify the join view's proposals by applying HoPF's candidate pruning rules unchanged.

The two filters are PK-in-PK (HoPF 5.2, the consecutive-subsequence test) and foreign-key
uniqueness (HoPF 5.4, one dependent references at most one key). Survivors are split into
documented and undocumented; undocumented ones are listed individually.

usage: adjudication.py [--work=$WORK_ROOT] [--out=paper/tables/adjudication.txt]
"""
import csv
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "experiments" / "lib"))
import paths  # noqa: E402
sys.path.insert(0, str(HERE / "profile"))
import dependency_key  # noqa: E402
import entailed  # noqa: E402
import result_kind  # noqa: E402

TRAILING_COMMA = re.compile(r",\s*([}\]])")
csv.field_size_limit(1 << 24)

INSTANCES = [
    ("TPC-H", "mm_tpch", "compare/mm-1.0/ind-results", "mm_tpch/ground_truth.json"),
    ("UniBench", "unibench", "unibench/meta-results-0.5", "unibench/ground_truth.json"),
    ("IMDB", "imdb", "imdb/eq-1.0-n1", "imdb/ground_truth.json"),
    ("MusicBrainz", "musicbrainz", "musicbrainz/eq-1.0-n1", "musicbrainz/ground_truth.json"),
    ("MMSBench-Net", "coms", "coms/meta-results-1.0", "coms/ground_truth.json"),
    ("MovieLens", "movielens", "movielens/meta-results-0.5", "movielens/ground_truth.json"),
    ("Helsinki Patent", "hpatent", "hpatent/meta-results-0.5", "hpatent/ground_truth.json"),
    ("ListenBrainz", "listenbrainz", "listenbrainz/meta-results-0.5",
     "listenbrainz/ground_truth.json"),
]


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def numeric(values):
    """The values as a sorted list of numbers, or None if any of them is not numeric."""
    out = []
    for value in values:
        try:
            out.append(int(value))
        except ValueError:
            try:
                out.append(float(value))
            except ValueError:
                return None
    return sorted(out)


def consecutive_subsequence(left, right, dependent_is_unique):
    """HoPF 5.2: whether the dependent is a key of its own whose values are a run of the other's.

    The test is positional rather than arithmetic: the values must occupy adjacent positions in
    the referenced side's sorted order, so it also works on keys with gaps. The uniqueness
    precondition is HoPF's; without it, a repeating foreign key such as `c_nationkey` (25 values,
    each repeated many times) would be dropped as PK-in-PK.
    """
    if not dependent_is_unique:
        return False
    small, large = numeric(left), numeric(right)
    if small is None or large is None or not small:
        return False
    position = {value: index for index, value in enumerate(large)}
    try:
        indices = sorted(position[value] for value in small)
    except KeyError:
        return False
    return indices == list(range(indices[0], indices[0] + len(indices)))


def surprise(left, right, domain_size):
    """How improbable the observed overlap is, the instance's own comparison domain as the null."""
    overlap = len(left & right)
    if not domain_size or not overlap:
        return 0.0
    return result_kind._log10_binomial_tail(len(left), min(1.0, len(right) / domain_size), overlap)


def column_stats(export):
    catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    values, support = {}, {}
    for entry in catalog:
        with (export / entry["file"]).open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            seen = [set() for _ in header]
            filled = [0] * len(header)
            total = 0
            for row in reader:
                total += 1
                for i, value in enumerate(row):
                    if i < len(header) and value != "":
                        seen[i].add(value)
                        filled[i] += 1
        for i, column in enumerate(header):
            name = entry["relation"] + "." + column
            values[name] = seen[i]
            support[name] = (filled[i], total)
    return values, support


def inclusions(results, values):
    text = TRAILING_COMMA.sub(r"\1", (results / "1-ary_pINDs.json").read_text(encoding="utf-8"))
    pairs = set()
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = relation["relation"] + "." + ".".join(pind["dependant"])
            if not values.get(dependent):
                continue
            for ref in pind["referenced"]:
                pairs.add((dependent, ref["relation"] + "." + ".".join(ref["attribute"])))
    return pairs


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    out = Path(flag("out", str(HERE / "paper" / "tables" / "adjudication.txt")))

    lines = [
        "# The join view, stratified by why each proposal is or is not in the oracle.",
        "# Generated by experiments/report/adjudication.py.",
        "#",
        "# Both filters are HoPF's candidate pruning rules applied unchanged to our join view:",
        "# PK-in-PK (its 5.2, the consecutive-subsequence test) and foreign-key uniqueness (5.4,",
        "# one dependent references at most one key). Neither was written for this evaluation.",
        "",
        "  %-18s %8s %8s %8s %6s %6s %6s %6s %8s %8s"
        % ("instance", "proposed", "PKinPK", "not uniq", "faint", "left", "doc", "undoc",
           "prec-doc", "prec-adj"),
    ]
    detail, totals = [], [0, 0, 0, 0, 0, 0, 0]
    for label, directory, results_rel, oracle_rel in INSTANCES:
        export = work / directory / "metalevel"
        results = work / results_rel
        oracle_path = work / oracle_rel
        if not export.exists() or not (results / "1-ary_pINDs.json").exists():
            lines.append("  %-18s not present" % label)
            continue
        values, support = column_stats(export)
        oracle = set()
        if oracle_path.exists():
            for entry in json.loads(oracle_path.read_text(encoding="utf-8"))["dependencies"]:
                if "dependent" in entry:
                    oracle.add(dependency_key.key(entry["dependent"], entry["referenced"]))
        pairs = inclusions(results, values)
        pairs -= entailed.entailed_pairs(export)
        domain = set()
        for value_set in values.values():
            domain |= value_set
        domain_size = len(domain)

        # Minimum support applies to both sides: uniqueness over a few rows is not evidence of a
        # key, and containment of one observed value is not evidence of a reference.
        proposed = [(d, r) for d, r in pairs
                    if result_kind.qualifies_as_reference_target(
                        len(values.get(r, ())), *support.get(r, (0, 0)))
                    and support.get(d, (0, 0))[0] >= result_kind.MINIMUM_SUPPORT]

        # HoPF 5.2, applied first because it does not depend on what else was proposed.
        kept, pk_in_pk = [], 0
        for dependent, referenced in proposed:
            dependent_is_unique = len(values[dependent]) == support.get(dependent, (0, 0))[0]
            if consecutive_subsequence(values[dependent], values.get(referenced, set()),
                                       dependent_is_unique):
                pk_in_pk += 1
            else:
                kept.append((dependent, referenced))

        # HoPF 5.4: one dependent, one reference; the survivor is the most surprising overlap.
        # Iterating in sorted order makes tie-breaking deterministic. Ties arise when the same
        # identifier is carried in several stores (e.g. `tags.movieid` inside both
        # `movies.movieid` and `links.movieid`); their count is reported since the choice is arbitrary.
        best, ties = {}, 0
        for dependent, referenced in sorted(kept):
            score = surprise(values[dependent], values.get(referenced, set()), domain_size)
            if dependent not in best:
                best[dependent] = (score, referenced)
            elif score > best[dependent][0]:
                best[dependent] = (score, referenced)
            elif score == best[dependent][0]:
                ties += 1
        singular = [(dependent, referenced) for dependent, (_, referenced) in
                    sorted(best.items())]
        dropped = len(kept) - len(singular)

        # Significance level: alpha = 0.05, Bonferroni-corrected over the ordered pairs tested by
        # unary discovery. It removes little because the null rewards rare values; the support
        # floor above does most of the filtering.
        tested = max(len(values) * (len(values) - 1), 1)
        level = -math.log10(0.05 / tested)
        survivors = [(d, r) for d, r in singular
                     if surprise(values[d], values.get(r, set()), domain_size) >= level]
        faint = len(singular) - len(survivors)

        documented = [p for p in survivors if dependency_key.key(*p) in oracle]
        undocumented = [p for p in survivors if dependency_key.key(*p) not in oracle]
        prec_doc = len(documented) / len(proposed) if proposed else 0.0
        prec_adj = len(documented) / len(survivors) if survivors else 0.0
        lines.append("  %-18s %8d %8d %8d %6d %6d %6d %6d %8.2f %8.2f"
                     % (label, len(proposed), pk_in_pk, dropped, faint, len(survivors),
                        len(documented), len(undocumented), prec_doc, prec_adj)
                     + ("   %d tie(s) broken arbitrarily" % ties if ties else ""))
        for index, value in enumerate((len(proposed), pk_in_pk, dropped, faint, len(survivors),
                                       len(documented), len(undocumented))):
            totals[index] += value
        for dependent, referenced in sorted(undocumented):
            detail.append((label, dependent, referenced,
                           len(values[dependent] & values.get(referenced, set())) /
                           max(len(values[dependent]), 1),
                           len(values[dependent]), len(values.get(referenced, set()))))

    lines.append("  %-18s %8d %8d %8d %6d %6d %6d %6d %8.2f %8.2f"
                 % ("overall", totals[0], totals[1], totals[2], totals[3], totals[4], totals[5],
                    totals[6],
                    totals[5] / totals[0] if totals[0] else 0.0,
                    totals[5] / totals[4] if totals[4] else 0.0))

    lines += [
        "",
        "## the proposals that survive both filters and are not in any oracle",
        "  Reported individually because a reader cannot check a ratio. Each is an inclusion whose",
        "  referenced side is a candidate key, whose values are not a consecutive run of it, and",
        "  which is the most surprising of its dependent's candidates. Whether it is a reference is",
        "  a question about the data, not about the discovery.",
        "",
        "  %-16s %-42s %-38s %9s %9s %9s"
        % ("instance", "dependent", "referenced", "coverage", "|dep|", "|ref|"),
    ]
    for label, dependent, referenced, coverage, left, right in detail:
        lines.append("  %-16s %-42s %-38s %9.4f %9d %9d"
                     % (label, dependent[-42:], referenced[-38:], coverage, left, right))

    lines += [
        "",
        "## reading",
        "  'prec-doc' is precision over everything proposed, charging every undocumented proposal",
        "  as an error. 'prec-adj' is precision over what survives the two published filters. The",
        "  gap between them is the part of the apparent error that the filters explain, and the",
        "  remaining undocumented proposals are listed above rather than assumed to be either.",
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("written to " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
