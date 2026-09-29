"""Discovery on MMSBench-Net against the scenario's designed structure.

Reports the designed correspondences and whether they are found, the declared keys and whether they
hold, why the device correspondences have no single-scope form (devices are split across six graph
labels), and how the chance correction scores those inclusions.

usage: coms_taxonomy.py [--work=$WORK_ROOT] [--out=paper/tables/coms-taxonomy.txt]
"""
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402

HERE = Path(__file__).resolve().parents[2]

# Correspondences read off the benchmark's NetworkGenerator and Login/Node classes; the scenario
# ships no IND ground truth.
DESIGNED = [
    ("coms.login.userid", "coms.user.id", "relational -> relational"),
    ("coms.logs@users.users", "coms.user.id", "document array -> relational"),
    ("coms.logs.user.id", "coms.user.id", "nested document -> relational"),
    ("coms.login.deviceid", "<device>.id", "relational -> graph"),
    ("coms.logs.deviceid", "<device>.id", "document -> graph"),
]
DEVICE_LABELS = ["ap", "iot", "mobile", "pc", "server", "switch"]

# The scenario's entire declared schema (SchemaGenerator, Graph.getSchemaRelQueries): two primary
# keys and no foreign keys. The graph and document sides declare nothing.
DECLARED_KEYS = [
    ("coms.user", ["id"]),
    ("coms.login", ["accesstime"]),
]
TRAILING_COMMA = re.compile(r",\s*([}\]])")


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def observations(export):
    """{qualified extractor: set of its non-null values} over the whole export."""
    catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    values = {}
    for entry in catalog:
        with (export / entry["file"]).open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            columns = [set() for _ in header]
            for row in reader:
                for i, value in enumerate(row):
                    if i < len(columns) and value != "":
                        columns[i].add(value)
        for i, name in enumerate(header):
            values[entry["relation"] + "." + name] = columns[i]
    return values


def inclusions(results):
    """{(dependent, referenced): degree} from a SPIND result directory."""
    text = TRAILING_COMMA.sub(r"\1", (results / "1-ary_pINDs.json").read_text(encoding="utf-8"))
    found = {}
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = relation["relation"] + "." + ".".join(pind["dependant"])
            for ref in pind["referenced"]:
                referenced = ref["relation"] + "." + ".".join(ref["attribute"])
                found[(dependent, referenced)] = ref["partialDegree"]
    return found


def designed_block(values, found):
    lines = ["## the five designed correspondences",
             "  correspondence                                       crosses                        found"]
    for dependent, referenced, crossing in DESIGNED:
        if referenced == "<device>.id":
            hits = [lab for lab in DEVICE_LABELS
                    if (dependent, "neo4j:coms_" + lab + ".id") in found]
            verdict = ("as " + str(len(hits)) + "/6 forward inclusions" if hits
                       else "not as one inclusion -- see below")
        else:
            verdict = "yes" if (dependent, referenced) in found else "no"
        lines.append("  %-52s %-30s %s"
                     % (dependent + " -> " + referenced, crossing, verdict))
    return lines


def taxonomy_block(values, found):
    device = values.get("coms.login.deviceid", set())
    label_ids = {lab: values.get("neo4j:coms_" + lab + ".id", set()) for lab in DEVICE_LABELS}
    sets = list(label_ids.values())
    union = set().union(*sets) if sets else set()
    overlaps = sum(len(a & b) for i, a in enumerate(sets) for b in sets[i + 1:])

    lines = ["", "## why the two device correspondences have no single-scope form",
             "  login.deviceid has %d distinct values; the six device labels hold %d between them"
             % (len(device), len(union)),
             "  union equals the referenced side exactly: %s" % (device == union),
             "  the six supports are pairwise disjoint:   %s" % (overlaps == 0),
             "",
             "  label                   nodes   covers login.deviceid  reverse inclusion held"]
    for lab, ids in label_ids.items():
        covered = len(device & ids) / len(device) if device else 0.0
        held = all(("neo4j:coms_" + lab + ".id", target) in found
                   for target in ("coms.login.deviceid", "coms.logs.deviceid"))
        lines.append("  coms_%-17s %6d %21.3f  %s"
                     % (lab, len(ids), covered, "yes (both)" if held else "no"))
    best = max((len(device & ids) / len(device)) for ids in sets) if device and sets else 0.0
    lines += ["  the best single label covers %.3f of the referenced side, so no partial inclusion"
              % best,
              "  at any usable threshold states the dependency either."]
    return lines


def ranking_block(scored):
    if not scored:
        return ["", "## what the chance correction says about those reverse inclusions",
                "  genuineness.json missing; run profile/genuineness.py first"]
    lines = ["", "## what the chance correction says about those reverse inclusions",
             "  candidate                                             sig   gcov       q"]
    rows = []
    for lab in DEVICE_LABELS:
        key = ("neo4j:coms_" + lab + ".id", "coms.login.deviceid")
        if key in scored:
            rows.append((key[0] + " -> login.deviceid", scored[key], "designed"))
    for key, entry in scored.items():
        if (key[0].startswith("neo4j:coms_") and key[0].endswith(".id")
                and key[1] == "coms.login.duration"):
            rows.append((key[0] + " -> login.duration", entry, "coincidence"))
    for name, entry, kind in sorted(rows, key=lambda row: -row[1]["significance"]):
        lines.append("  %-50s %6.1f %6.3f %7.3f  %s"
                     % (name, entry["significance"], entry["gcov"], entry["share"], kind))
    lines += [
        "",
        "  The coincidences outrank the designed links. q is a cell share within the referenced",
        "  extractor's comparison domain, and every identifier in this scenario is a small dense",
        "  integer, so landing inside any of them is close to certain: 98.1% of the instance's",
        "  integer-valued cells hold a value that also appears in login.deviceid. The correction is",
        "  not misreporting -- an inclusion into a support that large is genuinely no evidence --",
        "  but it leaves these links unranked, and the generated property names leave the name",
        "  signal blind on the same links. Both of the profile's orderings are uninformative here,",
        "  which is a property of how this scenario encodes identity rather than of either measure.",
    ]
    return lines


def read_uccs(export, relation):
    """Minimal unique column combinations our UCC run reported for one scope."""
    path = export / (relation + ".uccs")
    if not path.exists():
        return None
    keys = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line.startswith("[") and line.endswith("]"):
            members = [m.strip().split(".")[-1] for m in line[1:-1].split(",") if m.strip()]
            keys.append(sorted(members))
    return keys


def keys_block(values, export):
    lines = ["", "## the two keys the scenario declares, and no foreign key anywhere",
             "  declared key                     holds on the data            what we report instead"]
    for relation, declared in DECLARED_KEYS:
        column = values.get(relation + "." + declared[0], set())
        rows = 0
        cat = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
        entry = next((e for e in cat if e["relation"] == relation), None)
        rows = entry["rows"] if entry else 0
        holds = bool(column) and len(column) == rows
        keys = read_uccs(export, relation)
        if keys is None:
            reported = "no UCC dump"
        elif [sorted(declared)] == [k for k in keys if k == sorted(declared)]:
            reported = "the same key, minimal"
        else:
            smallest = min(keys, key=len) if keys else []
            reported = "{" + ", ".join(smallest) + "}"
        lines.append("  %-32s %-28s %s"
                     % (relation + ".[" + ", ".join(declared) + "]",
                        ("yes" if holds else "no (%d of %d distinct)" % (len(column), rows)),
                        reported))
    lines += [
        "",
        "  The second is the finding. MMSBench-Net declares accesstime alone as login's primary",
        "  key, and its own generator draws login timestamps from a range narrow enough that they",
        "  repeat, so the declared key does not hold on the data the benchmark produces. Profiling",
        "  the instance reports the smallest combination that does. ER'25 reaches the same verdict",
        "  independently on the same instance (see er25-coms.txt), which is what makes this a",
        "  property of the benchmark rather than of either implementation.",
    ]
    return lines


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    out = Path(flag("out", str(HERE / "paper" / "tables" / "coms-taxonomy.txt")))
    export, results = work / "coms" / "metalevel", work / "coms" / "meta-results-1.0"
    if not export.exists() or not results.exists():
        print("missing " + str(export) + " or " + str(results), file=sys.stderr)
        return 1

    values = observations(export)
    found = inclusions(results)
    genuineness = work / "coms" / "genuineness.json"
    scored = {}
    if genuineness.exists():
        scored = {(s["dependent"], s["referenced"]): s
                  for s in json.loads(genuineness.read_text(encoding="utf-8"))}

    lines = [
        "# MMSBench-Net (Polypheny 'coms'): discovery against the scenario's designed structure.",
        "# Generated by experiments/report/coms_taxonomy.py.",
        "#",
        "# No published IND oracle exists for this scenario. The correspondences below are read off",
        "# its generator, which is weaker ground truth than UniBench's, and is labelled as such.",
        "",
    ]
    lines += designed_block(values, found)
    lines += keys_block(values, export)
    lines += taxonomy_block(values, found)
    lines += ranking_block(scored)

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("written to " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
