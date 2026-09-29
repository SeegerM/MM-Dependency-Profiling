"""ER'25 against this work on MMSBench-Net (see er25_compare.py for UniBench).

As shipped, ER'25 does not complete on this instance:

  1. UACFinder aborts on a datastorage with no attributes of its own (a nested document path whose
     children are all objects): the empty list becomes `... IN ()`, MariaDB rejects it, and the
     None returned by DBConnector.query is subscripted. Guarded in Dockerfile.local via
     apply-patches.py; the guard changes no result on inputs that did not previously crash.
  2. With shipped settings (speed_mode 0, find_max_ind true), the maximal-IND search is killed by
     the OOM killer after about 100 s, driven by the 321 attributes of the schemaless log collection.
  3. With find_max_ind false, the UAC and IND phases complete, and the foreign-key ranker aborts on
     type-heterogeneous document keys (`ValueError: invalid literal for int()`). This is not
     patched, so no ER'25 reference ranking is reported.

The comparison uses run 3, where both systems compute unary inclusions.

usage: er25_coms.py [--work=$WORK_ROOT] [--out=paper/tables/er25-coms.txt]
"""
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402

HERE = Path(__file__).resolve().parents[2]
TRAILING_COMMA = re.compile(r",\s*([}\]])")

# Correspondences read off the generator (NetworkGenerator, Login/Node); no IND oracle is published.
DESIGNED = [
    ("coms.login.userid", "coms.user.id"),
    ("coms.logs@users.users", "coms.user.id"),
    ("coms.logs.user.id", "coms.user.id"),
]
DEVICE_LABELS = ["ap", "iot", "mobile", "pc", "server", "switch"]


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def read_tsv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def extractor_names(export):
    """Every qualified extractor our export defines, used to reconcile ER'25's naming with ours."""
    catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    names = set()
    for entry in catalog:
        with (export / entry["file"]).open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
        for column in header:
            names.add(entry["relation"] + "." + column)
    return names


def qualify(rows_by_id, storage_id, server_type, attribute, known):
    """ER'25's naming for one attribute, rendered the way our export names the same thing.

    ER'25's datastorages form a tree, so a nested path is walked up to its collection and dotted;
    an array attribute (`logs.users`) maps to our exploded scope (`logs@users.users`). A name that
    matches no known extractor is returned as built, so disagreements stay visible.
    """
    path = []
    current = storage_id
    seen = set()
    while current and current in rows_by_id and current not in seen:
        seen.add(current)
        row = rows_by_id[current]
        path.append(row["storage_name"])
        current = row["parent_id"] if row["parent_id"] not in ("NULL", "", None) else None
    path.reverse()
    if server_type == "neo4j":
        return "neo4j:" + path[-1] + "." + attribute
    dotted = "coms." + ".".join(path + [attribute])
    if dotted in known:
        return dotted
    exploded = "coms." + ".".join(path) + "@" + attribute + "." + attribute
    return exploded if exploded in known else dotted


def unique_extractors(export):
    """Extractors whose non-null values are distinct, i.e. unary candidate keys of their scope."""
    catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    unique = set()
    for entry in catalog:
        with (export / entry["file"]).open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            seen = [set() for _ in header]
            filled = [0] * len(header)
            for row in reader:
                for i, value in enumerate(row):
                    if i < len(header) and value != "":
                        seen[i].add(value)
                        filled[i] += 1
        for i, column in enumerate(header):
            if filled[i] and len(seen[i]) == filled[i]:
                unique.add(entry["relation"] + "." + column)
    return unique


def ours(results):
    text = TRAILING_COMMA.sub(r"\1", (results / "1-ary_pINDs.json").read_text(encoding="utf-8"))
    found = set()
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = relation["relation"] + "." + ".".join(pind["dependant"])
            for ref in pind["referenced"]:
                found.add((dependent, ref["relation"] + "." + ".".join(ref["attribute"])))
    return found


def theirs(er25_dir, known):
    storages = {row["id"]: row for row in read_tsv(er25_dir / "datastorage.tsv")}
    pairs = set()
    unary = 0
    for row in read_tsv(er25_dir / "inds.tsv"):
        child = row["child_attribute_names"]
        parent = row["attribute_names"]
        # Only unary inclusions are comparable with our unary run.
        if "," in child or "," in parent:
            continue
        unary += 1
        pairs.add((qualify(storages, row["child_datastorage_id"],
                           row["child_server_type"], child, known),
                   qualify(storages, row["datastorage_id"],
                           row["server_type"], parent, known)))
    return pairs, unary


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    out = Path(flag("out", str(HERE / "paper" / "tables" / "er25-coms.txt")))
    base = work / "coms-iso"
    if not (base / "er25" / "inds.tsv").exists() or not (base / "meta-results-1.0").exists():
        print("missing " + str(base) + "; run the ER'25 job and our discovery first",
              file=sys.stderr)
        return 1

    known = extractor_names(base / "metalevel")
    mine = ours(base / "meta-results-1.0")
    other, unary_rows = theirs(base / "er25", known)

    lines = [
        "# ER'25 (ref-seeker-extractor) and this work on MMSBench-Net, same instance, same stores.",
        "# Generated by experiments/report/er25_coms.py.",
        "#",
        "# Both systems read the same three containers holding this scenario and nothing else.",
        "# ER'25 is run with find_max_ind false so that both are asked for unary inclusions; see",
        "# the module docstring for the two configurations that do not complete at all.",
        "",
        "## what each system produced",
        "  %-46s %s" % ("this work, unary inclusions at rho = 1.0", len(mine)),
        "  %-46s %s" % ("ER'25, unary inclusions", len(other)),
        "  %-46s %s" % ("ER'25 rows read (n-ary combinations dropped)", unary_rows),
        "",
        "## the correspondences the generator establishes",
        "  %-40s %-14s %s" % ("correspondence", "this work", "ER'25"),
    ]
    for dependent, referenced in DESIGNED:
        lines.append("  %-40s %-14s %s"
                     % (dependent.replace("coms.", "") + " -> " + referenced.replace("coms.", ""),
                        "found" if (dependent, referenced) in mine else "missed",
                        "found" if (dependent, referenced) in other else "missed"))
    for target in ("coms.login.deviceid", "coms.logs.deviceid"):
        m = sum(1 for lab in DEVICE_LABELS if ("neo4j:coms_" + lab + ".id", target) in mine)
        t = sum(1 for lab in DEVICE_LABELS if ("neo4j:coms_" + lab + ".id", target) in other)
        lines.append("  %-40s %-14s %s"
                     % ("device labels -> " + target.replace("coms.", ""),
                        str(m) + "/6 labels", str(t) + "/6 labels"))

    # ER'25 only states inclusions into a unique attribute combination; count how many of ours
    # have a non-key referenced side and so are outside what its output can express.
    unique = unique_extractors(base / "metalevel")
    into_key = {pair for pair in mine if pair[1] in unique}
    lines += [
        "",
        "## how much of the difference is the shape of the question",
        "  %-46s %s" % ("ours whose referenced side is a unary key", len(into_key)),
        "  %-46s %s" % ("ours whose referenced side is not", len(mine) - len(into_key)),
        "  ER'25 states every inclusion against a unique attribute combination, so the second",
        "  row is outside what its output can express. The six device-label inclusions into",
        "  login.deviceid are in that row: a device appears in many logins, so the referenced",
        "  side is not a key, and the correspondence is unreachable for it by construction",
        "  rather than missed.",
    ]

    # The scenario declares two primary keys and no foreign key; compare ER'25's recovered keys.
    uacs = read_tsv(base / "er25" / "uacs.tsv") if (base / "er25" / "uacs.tsv").exists() else []
    if uacs:
        lines += ["", "## the two keys the scenario declares, recovered from the data",
                  "  %-34s %s" % ("declared key", "ER'25's top-scoring key for that table")]
        for storage, declared in (("user", "id"), ("login", "accesstime")):
            best = [row for row in uacs
                    if row["datastorage_name"] == storage and row["server_type"] == "PostgreSQL"]
            top = max(best, key=lambda row: int(row["pk_score_hopf"] or 0), default=None)
            lines.append("  %-34s %s" % (storage + ".[" + declared + "]",
                                         "{" + top["attribute_names"] + "}" if top else "none"))
        lines += [
            "  ER'25 recovers user.id, and for login reports the same three-column key we do",
            "  rather than the declared accesstime, which the generated data does not satisfy.",
        ]

    both = mine & other
    lines += [
        "",
        "## overlap",
        "  %-46s %s" % ("found by both", len(both)),
        "  %-46s %s" % ("only by this work", len(mine - other)),
        "  %-46s %s" % ("only by ER'25", len(other - mine)),
    ]
    if other - mine:
        lines.append("")
        lines.append("  inclusions ER'25 reports and we do not:")
        for dependent, referenced in sorted(other - mine)[:8]:
            lines.append("    %s -> %s" % (dependent, referenced))

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("written to " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
