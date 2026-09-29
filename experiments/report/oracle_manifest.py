"""Generate the oracle audit and the paper's instance-table rows.

Per instance, oracle entries are split by provenance (publisher-documented vs reconstructed), arity
(unary vs n-ary), and statability; result sets are reported raw and non-vacuous (empty dependents
removed). The LaTeX table is generated alongside the audit.

usage: oracle_manifest.py [--work=$WORK_ROOT] [--out=paper/tables/oracle-manifest.txt]
                          [--latex=paper/tables/oracle-instances.tex]
"""
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "experiments" / "lib"))
import paths  # noqa: E402
import dependency_key  # noqa: E402
import entailed  # noqa: E402

TRAILING_COMMA = re.compile(r",\s*([}\]])")
csv.field_size_limit(1 << 24)

# (label, instance directory, the results the paper reports for it, rho of that run)
INSTANCES = [
    ("TPC-H", "mm_tpch", "compare/mm-1.0/ind-results", 1.0),
    ("UniBench", "unibench", "unibench/meta-results-0.5", 0.5),
    ("IMDB", "imdb", "imdb/eq-1.0-n1", 1.0),
    ("MusicBrainz", "musicbrainz", "musicbrainz/eq-1.0-n1", 1.0),
    ("IMDB (full)", "imdb-full", "imdb-full/eq-0.999-n1", 0.999),
    ("MusicBrainz (full)", "musicbrainz-full", "musicbrainz-full/eq-1.0", 1.0),
    ("MMSBench-Net", "coms", "coms/meta-results-1.0", 1.0),
    # LDBC is scored on the export with discovered unions: its supertype references have no
    # scope in the declared export (see scope-discovery.txt).
    ("LDBC SNB", "ldbc", "ldbc/meta-results-1.0-discovered", 1.0),
    ("MovieLens", "movielens", "movielens/meta-results-0.5", 0.5),
    # The four Helsinki datasets must stay adjacent: DISPLAY_LABEL indents Person, Film and
    # University under Patent.
    ("Helsinki Patent", "hpatent", "hpatent/meta-results-0.5", 0.5),
    ("Helsinki Person", "hperson", "hperson/meta-results-0.5/ind-results", 0.5),
    ("Helsinki Film", "hfilm", "hfilm/meta-results-0.5/ind-results", 0.5),
    ("Helsinki Univ.", "huniversity", "huniversity/meta-results-0.5/ind-results", 0.5),
    ("ListenBrainz", "listenbrainz", "listenbrainz/meta-results-0.5", 0.5),
]

GROUND_TRUTH = {
    "TPC-H": "seeded",
    "UniBench": "labelled",
    "IMDB": "converted",
    "MusicBrainz": "converted",
    "IMDB (full)": "converted",
    "MusicBrainz (full)": "converted",
    "MMSBench-Net": "native",
    "LDBC SNB": "converted",
    "MovieLens": "publisher",
    "Helsinki Patent": "publisher",
    "ListenBrainz": "publisher",
    "Helsinki Person": "publisher",
    "Helsinki Film": "publisher",
    "Helsinki Univ.": "publisher",
}

FOOTNOTE = {
    "TPC-H": r"$^{\ast}$",
    "IMDB (full)": r"$^{\dagger}$",
    "MusicBrainz (full)": r"$^{\|}$",
    "MMSBench-Net": r"$^{\ddagger}$",
    "LDBC SNB": r"$^{\ddagger}$",
    "MovieLens": r"$^{\S}$",
    "Helsinki Patent": r"$^{\P}$",
    "ListenBrainz": r"$^{\P}$",
}

# Instances scored on an export other than metalevel/.
EXPORT = {"LDBC SNB": "metalevel-discovered"}

DISPLAY_LABEL = {
    "Helsinki Patent": "Helsinki MM: Patent",
    "Helsinki Person": r"\quad Person",
    "Helsinki Film": r"\quad Film",
    "Helsinki Univ.": r"\quad University",
}

# Real alignments that do not assert inclusion. Oracle files without an explicit oracle_kind
# field are classified here.
CORRESPONDENCE_BOUNDARY = {
    dependency_key.key("hfilm.films.title", "neo4j:FilmDBpedia.id"),
    dependency_key.key("hfilm.films.title", "neo4j:FilmYago.id"),
    dependency_key.key("huniversity.scorecard.instnm", "neo4j:ULocation.id"),
    dependency_key.key("huniversity.scorecard.latitude", "neo4j:ULocation.latitude"),
}


def flag(name, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def latex_number(value):
    return f"{value:,}".replace(",", r"{,}")


def to_discovered_unions(entries, oracle_doc, export):
    """Points oracle references to a declared union at the discovered union with the same members.

    Discovery names its unions discovered.union-<n>; the oracle names them after the schema
    (ldbc.place-union). A union is the same scope when its member scopes are the same, which is
    the test scope_discovery.py scores with; a union discovery did not propose stays unmatched.
    """
    catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    found = {frozenset(e["members"]): e["relation"] for e in catalog if e.get("members")}
    rename = {u["name"]: found.get(frozenset(u["members"])) for u in oracle_doc.get("unions", [])}
    out = []
    for e in entries:
        relation, column = e["referenced"].rsplit(".", 1)
        if rename.get(relation):
            e = dict(e, referenced=rename[relation] + "." + column)
        out.append(e)
    return out


def support_counts(export):
    """Non-null count per extractor. Only enough to decide vacuity."""
    catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    filled = {}
    for entry in catalog:
        with (export / entry["file"]).open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            counts = [0] * len(header)
            for row in reader:
                for i, value in enumerate(row):
                    if i < len(header) and value != "":
                        counts[i] += 1
        for i, column in enumerate(header):
            filled[entry["relation"] + "." + column] = counts[i]
    return filled


def _identity(side):
    """One extractor's identity under the same normalisation the scoring key uses.

    An oracle and an export may spell the same extractor differently, so membership is tested on
    this identity rather than on raw strings.
    """
    scope, attributes = dependency_key._scope_and_attributes(side)
    return (dependency_key._canonical_scope(scope), tuple(attributes))


def run_pairs(results):
    text = TRAILING_COMMA.sub(r"\1", (results / "1-ary_pINDs.json").read_text(encoding="utf-8"))
    pairs = set()
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = relation["relation"] + "." + ".".join(pind["dependant"])
            for ref in pind["referenced"]:
                pairs.add((dependent, ref["relation"] + "." + ".".join(ref["attribute"])))
    return pairs


def score(export, results, statable, filled=None):
    """Scores one result set against the statable oracle entries of its instance.

    Returns (raw, non_vacuous, recovered, missed), where missed lists the statable entries no live
    inclusion recovers. Shared with experiments/report/tpch_threshold.py, so a run scored at another
    threshold is judged exactly as the manifest judges the reported one. `filled` is the export's
    support_counts, passed in when the caller already has it: counting reads the whole export.
    """
    filled = support_counts(export) if filled is None else filled
    pairs = run_pairs(results)
    # Drop inclusions that hold regardless of the data: an empty dependent is contained in
    # everything, and a union contains its members.
    entailed_here = entailed.entailed_pairs(export)
    live = {p for p in pairs if filled.get(p[0], 0) > 0 and p not in entailed_here}
    found = {dependency_key.key(*p) for p in live}
    missed = [e for e in statable if dependency_key.key(e["dependent"], e["referenced"]) not in found]
    oracle_keys = {dependency_key.key(e["dependent"], e["referenced"]) for e in statable}
    recovered = sum(1 for p in live if dependency_key.key(*p) in oracle_keys)
    return len(pairs), len(live), recovered, missed


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    out = Path(flag("out", str(HERE / "paper" / "tables" / "oracle-manifest.txt")))
    latex = Path(flag("latex", str(HERE / "paper" / "tables" / "oracle-instances.tex")))

    lines = [
        "# The manifest. Every oracle size and recall figure in the paper is read from here.",
        "# Generated by experiments/report/oracle_manifest.py.",
        "#",
        "# 'unary' is the denominator a unary run can be judged against; n-ary entries stay in the",
        "# oracle and out of that denominator. 'pub' and 'rec' split it by provenance: a publisher's",
        "# own artifact states the first, we established the second.",
        "",
        "  %-18s %6s %6s %5s %5s %8s %8s %6s %9s %9s %9s"
        % ("instance", "rho", "oracle", "unary", "n-ary", "statable", "boundary",
           "pub/rec", "raw", "non-vac", "recovered"),
    ]
    detail = []
    boundary_detail = []
    vacuous_detail = []
    latex_rows = []
    for label, directory, results_rel, rho in INSTANCES:
        oracle_path = work / directory / "ground_truth.json"
        export = work / directory / EXPORT.get(label, "metalevel")
        results = work / results_rel
        if not oracle_path.exists():
            lines.append("  %-18s no oracle" % label)
            continue
        oracle_doc = json.loads(oracle_path.read_text(encoding="utf-8"))
        all_entries = [e for e in oracle_doc["dependencies"] if "dependent" in e]
        if label in EXPORT:
            all_entries = to_discovered_unions(all_entries, oracle_doc, export)
        # Correspondences that do not assert inclusion are reported as boundary cases and
        # excluded from dependency recall.
        def is_boundary(entry):
            return (entry.get("oracle_kind") == "correspondence"
                    or dependency_key.key(entry["dependent"], entry["referenced"])
                    in CORRESPONDENCE_BOUNDARY)

        boundary = [e for e in all_entries if is_boundary(e)]
        entries = [e for e in all_entries if not is_boundary(e)]
        arity = Counter("n-ary" if int(e.get("arity", 1)) > 1 else "unary" for e in entries)
        provenance = Counter(e.get("provenance", "publisher") for e in entries
                             if int(e.get("arity", 1)) == 1)
        unary = [e for e in entries if int(e.get("arity", 1)) == 1]

        raw = non_vacuous = recovered = None
        statable = unary
        if export.exists() and (results / "1-ary_pINDs.json").exists():
            catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
            filled = support_counts(export)
            # Statable entries name extractors the export has, with a dependent that observes at
            # least one value (an empty dependent holds vacuously). Membership is tested on full
            # extractor names: column names may contain dots (`order_item.vendor.brand`), so
            # splitting at the last dot does not recover the scope.
            present = {_identity(name) for name in filled}
            observed = {_identity(name) for name, count in filled.items() if count > 0}
            statable = [e for e in unary
                        if _identity(e["dependent"]) in observed
                        and _identity(e["referenced"]) in present]
            vacuous_detail += [(label, e["dependent"], e["referenced"]) for e in unary
                               if _identity(e["dependent"]) in present
                               and _identity(e["dependent"]) not in observed]
            raw, non_vacuous, recovered, _ = score(export, results, statable, filled)

            scopes = len(catalog)
            extractors = sum(int(entry["columns"]) for entry in catalog)
            observations = sum(int(entry["rows"]) for entry in catalog)
            # Oracle size and statable subset are reported as separate columns.
            recovered_scope = (("%d/%d" % (recovered, len(statable)))
                               if statable else "--") + FOOTNOTE.get(label, "")
            latex_rows.append(
                "%s & %s & %d & %s & %s & %d & %d & %s & %d \\\\" % (
                    DISPLAY_LABEL.get(label, label), GROUND_TRUTH[label], scopes,
                    latex_number(extractors),
                    latex_number(observations),
                    len(entries), len(statable), recovered_scope, len(boundary)))

        lines.append("  %-18s %6g %6d %5d %5d %8d %8d %6s %9s %9s %9s"
                     % (label, rho, len(entries), arity["unary"], arity["n-ary"], len(statable),
                        len(boundary),
                        "%d/%d" % (provenance["publisher"], provenance["reconstructed"]),
                        "-" if raw is None else raw,
                        "-" if non_vacuous is None else non_vacuous,
                        "-" if recovered is None else "%d/%d" % (recovered, len(statable))))
        for entry in entries:
            if int(entry.get("arity", 1)) > 1:
                detail.append((label, entry["dependent"], entry["referenced"]))

        for entry in boundary:
            boundary_detail.append((label, entry["dependent"], entry["referenced"]))

    if detail:
        lines += [
            "",
            "## n-ary oracle entries, excluded from every unary denominator",
            "  These are correspondences a unary run cannot state. They remain in the oracle",
            "  because they are correspondences; they are not counted against a unary result set.",
            "",
        ]
        for label, dependent, referenced in detail:
            lines.append("  %-18s %-44s -> %s" % (label, dependent, referenced))

    if vacuous_detail:
        lines += [
            "",
            "## vacuous oracle entries (empty dependent), excluded from the statable denominator",
            "  Each holds in the raw result because an empty dependent is contained in everything;",
            "  it gives no evidence of the relationship.",
            "",
        ]
        for label, dependent, referenced in vacuous_detail:
            lines.append("  %-18s %-44s -> %s" % (label, dependent, referenced))

    if boundary_detail:
        lines += [
            "",
            "## correspondence boundary cases, excluded from dependency recall",
            "  These real cross-source correspondences do not assert inclusion. They test the",
            "  boundary of dependency discovery rather than its recall.",
            "",
        ]
        for label, dependent, referenced in boundary_detail:
            lines.append("  %-18s %-44s -> %s" % (label, dependent, referenced))

    lines += [
        "",
        "## reading",
        "  'oracle' is every documented and reconstructed correspondence. 'unary' is the subset a",
        "  unary run could state, and is the denominator of every recall figure in the paper.",
        "  'raw' is what the algorithm emitted; 'non-vac' removes the inclusions whose dependent",
        "  observes nothing, and is the result set the paper reports over.",
        "  'boundary' lists real cross-source correspondences that do not assert inclusion and",
        "  are therefore excluded from both the dependency denominator and dependency recall.",
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if len(latex_rows) != len(INSTANCES):
        raise RuntimeError("cannot generate instance table: only %d/%d rows have data and results"
                           % (len(latex_rows), len(INSTANCES)))
    latex.parent.mkdir(parents=True, exist_ok=True)
    # Emit the whole tabular, not just its rows: \input inside an alignment breaks the following
    # \bottomrule ("Misplaced \noalign").
    latex.write_text(
        "% Generated by experiments/report/oracle_manifest.py; do not edit by hand.\n"
        "\\begin{tabular}{llrrrrrrr}\n"
        "\\toprule\n"
        " & & & & & \\multicolumn{3}{c}{dependency entries} & \\\\\n"
        "\\cmidrule(lr){6-8}\n"
        "instance & assembly & scopes & $|\\mathcal{E}|$ & observations"
        " & all & statable & recovered & boundary \\\\\n"
        "\\midrule\n"
        + "\n".join(latex_rows) + "\n"
        "\\bottomrule\n"
        "\\end{tabular}\n", encoding="utf-8")
    print("written to " + str(out))
    print("written to " + str(latex))
    return 0


if __name__ == "__main__":
    sys.exit(main())
