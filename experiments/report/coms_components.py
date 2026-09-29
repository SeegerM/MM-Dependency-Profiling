"""The component ablation on MMSBench-Net, with ER'25 scored on the same oracle.

`statable` counts the oracle entries a representation can express at all (an entry naming a scope
it does not build is outside the candidate space); `recovered` counts those the run reports.
Precision is taken over the join view (inclusions whose referenced side is a candidate key), not
over all reported inclusions, which the instance's dense integer identifiers inflate.

usage: coms_components.py [--work=$WORK_ROOT] [--out=paper/tables/coms-components.txt]
                          [--tex=paper/tables/coms-components.tex]
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "experiments" / "report"))
sys.path.insert(0, str(HERE / "experiments" / "lib"))
import paths  # noqa: E402

sys.path.insert(0, str(HERE / "profile"))

import adjudication as A  # noqa: E402
import dependency_key  # noqa: E402
import entailed  # noqa: E402
import er25_coms  # noqa: E402
import result_kind  # noqa: E402

TRAILING_COMMA = re.compile(r",\s*([}\]])")

# (label, export, result set). The two exports differ in exactly one scope: the device union.
CONFIGURATIONS = [
    ("scoped", "exact", "coms/metalevel-before-union", "coms/meta-results-1.0-before-union"),
    ("scoped", "partial, $\\rho=0.5$", "coms/metalevel-before-union",
     "coms/meta-results-0.5-before-union"),
    ("scoped + union", "exact", "coms/metalevel", "coms/meta-results-1.0"),
    ("scoped + union", "partial, $\\rho=0.5$", "coms/metalevel", "coms/meta-results-0.5"),
]


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def oracle_entries(path):
    blob = json.loads(Path(path).read_text(encoding="utf-8"))
    return [(e["dependent"], e["referenced"], e.get("union_target", False))
            for e in blob["dependencies"] if "dependent" in e]


def scopes_of(export):
    catalog = json.loads((Path(export) / "catalog.json").read_text(encoding="utf-8"))
    return {entry["relation"] for entry in catalog}


def inclusions(results):
    text = TRAILING_COMMA.sub(r"\1", (Path(results) / "1-ary_pINDs.json").read_text(encoding="utf-8"))
    found = set()
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = relation["relation"] + "." + ".".join(pind["dependant"])
            for ref in pind["referenced"]:
                found.add((dependent, ref["relation"] + "." + ".".join(ref["attribute"])))
    return found


def statable(entry, scopes):
    """Whether a representation that builds `scopes` can express this dependency at all.

    If either side's scope is not built, the pair is outside the candidate space.
    """
    for column in (entry[0], entry[1]):
        # Extractor names contain dots too (`coms.logs.user.id`), so match on scope prefix
        # rather than splitting on the last dot.
        if not any(column == scope or column.startswith(scope + ".") for scope in scopes):
            return False
    return True


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    out = Path(flag("out", str(HERE / "paper" / "tables" / "coms-components.txt")))
    tex = Path(flag("tex", str(HERE / "paper" / "tables" / "coms-components.tex")))

    entries = oracle_entries(work / "coms" / "ground_truth.json")
    keys = {dependency_key.key(d, r): (d, r, u) for d, r, u in entries}
    total = len(entries)

    rows = []
    for representation, validity, export_rel, results_rel in CONFIGURATIONS:
        export, results = work / export_rel, work / results_rel
        if not (export / "catalog.json").exists() or not (results / "1-ary_pINDs.json").exists():
            rows.append((representation, validity, None, None, None, None,
                     None, []))
            continue
        scopes = scopes_of(export)
        can_state = [e for e in entries if statable(e, scopes)]
        found = inclusions(results)
        found_keys = {dependency_key.key(d, r) for d, r in found}
        hit = [e for e in can_state if dependency_key.key(e[0], e[1]) in found_keys]

        # The join view: inclusions whose referenced side is a candidate key. ER'25 states every
        # inclusion against a unique attribute combination, so its whole output is a join view.
        values, support = A.column_stats(export)
        proposals = {(d, r) for d, r in found - entailed.entailed_pairs(export)
                     if result_kind.qualifies_as_reference_target(
                         len(values.get(r, ())), *support.get(r, (0, 0)))
                     and support.get(d, (0, 0))[0] >= result_kind.MINIMUM_SUPPORT}
        proposal_keys = {dependency_key.key(d, r) for d, r in proposals}
        keyed_hit = [e for e in can_state if dependency_key.key(e[0], e[1]) in proposal_keys]
        rows.append((representation, validity, len(can_state), len(hit), len(found),
                     len(proposals), len(keyed_hit), [e for e in can_state if e not in hit]))

    # ER'25 on the same oracle, from the isolated copy both systems were run on.
    er25 = None
    base = work / "coms-iso"
    if (base / "er25" / "inds.tsv").exists():
        known = er25_coms.extractor_names(base / "metalevel")
        pairs, unary_rows = er25_coms.theirs(base / "er25", known)
        their_keys = {dependency_key.key(d, r) for d, r in pairs}
        # ER'25 reads the stores' own tables and cannot name a union scope, so union-target
        # entries are unstatable for it rather than misses.
        er25_statable = [e for e in entries if not e[2]]
        er25_hit = [e for e in er25_statable if dependency_key.key(e[0], e[1]) in their_keys]
        er25 = (len(er25_statable), len(er25_hit), len(pairs), unary_rows)

    lines = [
        "# MMSBench-Net component ablation, against the generator's nine correspondences.",
        "# Generated by experiments/report/coms_components.py.",
        "#",
        "# 'statable' is how many of the nine the representation can express at all: an oracle",
        "# entry naming a scope the representation does not build is outside the candidate space,",
        "# not a low-ranked candidate. 'reported' is the whole result set; 'join view' is the",
        "# subset whose referenced side is a candidate key, which is the set precision means",
        "# anything over and the only one comparable with ER'25's output.",
        "",
        "  %-16s %-18s %9s %10s %10s %10s %9s %10s"
        % ("representation", "validity", "statable", "recovered", "reported", "join view",
           "in view", "view prec"),
    ]
    for representation, validity, can, hit, reported, view, keyed, missed in rows:
        if can is None:
            lines.append("  %-16s %-18s   run not present" % (representation, validity))
            continue
        lines.append("  %-16s %-18s %6d/%d %7d/%d %10s %10s %6d/%d %10.3f"
                     % (representation, validity.replace("$\\rho=0.5$", "rho=0.5"),
                        can, total, hit, total, format(reported, ","), format(view, ","),
                        keyed, total, keyed / view if view else 0.0))
    if er25:
        can, hit, reported, unary_rows = er25
        lines.append("  %-16s %-18s %6d/%d %7d/%d %10s %10s %6d/%d %10.3f"
                     % ("ER'25", "exact", can, total, hit, total, format(reported, ","),
                        format(reported, ","), hit, total, hit / reported if reported else 0.0))

    lines += ["", "## what each configuration cannot state, and what it misses"]
    for representation, validity, can, hit, reported, view, keyed, missed in rows:
        if can is None:
            continue
        lines.append("  %-16s %-18s statable %d/%d, recovered %d, join view %s"
                     % (representation, validity.replace("$\\rho=0.5$", "rho=0.5"),
                        can, total, hit, format(view, ",")))
        for entry in missed:
            lines.append("      statable but not recovered: %s -> %s" % (entry[0], entry[1]))
    unstatable = [e for e in entries if e[2]]
    lines.append("  the %d entries no base-scope representation can state, all naming the union:"
                 % len(unstatable))
    for entry in unstatable:
        lines.append("      %s -> %s" % (entry[0], entry[1]))
    if er25:
        lines.append("  ER'25 cannot state those six either: it reads each store's own tables, so")
        lines.append("      a union of six graph labels is not a population it can name. Its 283")
        lines.append("      inclusions are all stated against a unique attribute combination, so")
        lines.append("      its whole output is a join view and is compared with ours as one.")

    lines += [
        "",
        "## reading",
        "  Relaxation does nothing on this instance and the union does everything, which is the",
        "  mirror image of TPC-H. Every correspondence here holds exactly or not at all: the",
        "  generator writes a device identifier or it does not, so rho = 0.5 roughly doubles the",
        "  result set without adding a single oracle hit. What the base-scope representation lacks",
        "  is not a looser threshold but a population, and no threshold reaches a pair of scopes",
        "  that does not exist.",
        "",
        "  Precision against the whole result set is not reported, because on this instance it",
        "  would measure the identifier space rather than the discovery: the generator plants nine",
        "  correspondences and identifies every entity by a small dense integer, so thousands of",
        "  further inclusions are true and uninteresting. The join view is where the question",
        "  'is this a reference' is being asked, and it is what ER'25's output already is.",
    ]

    text = "\n".join(lines) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    body = ["\\begin{tabular}{llrrrr}", "\\toprule",
            "representation & validity & statable & recovered & reported & join view \\\\",
            "\\midrule"]
    for representation, validity, can, hit, reported, view, keyed, _missed in rows:
        if can is None:
            continue
        recovered = "\\textbf{%d/%d}" % (hit, total) if hit == total else "%d/%d" % (hit, total)
        body.append("%s & %s & %d/%d & %s & %s & %s \\\\"
                    % (representation, validity, can, total, recovered,
                       format(reported, ","), format(view, ",")))
    if er25:
        body.append("\\addlinespace[2pt]")
        body.append("ER'25~\\cite{DBLP:conf/er/HauslerCSSK25} & exact & %d/%d & %d/%d & %s & %s \\\\"
                    % (er25[0], total, er25[1], total, format(er25[2], ","),
                       format(er25[2], ",")))
    body += ["\\bottomrule", "\\end{tabular}"]
    tex.write_text("\n".join(body) + "\n", encoding="utf-8")

    print(text)
    print("written to %s and %s" % (out, tex))


if __name__ == "__main__":
    main()
