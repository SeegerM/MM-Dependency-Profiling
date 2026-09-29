"""Ablate oracle-independent ranking features on the MusicBrainz candidate set.

The discovery and definition-based prefilter are held fixed.  This script only reorders the 11,034
remaining candidates and evaluates how quickly the published FK list is recovered.  The oracle is
used for evaluation, never as a feature.

Features are added with equal-weight rank aggregation (Borda over normalized ranks), avoiding
weights fitted on MusicBrainz:

* significance: the existing null-relative inclusion score;
* structure: referenced-key utilization, dependent presence, a dependent-key penalty, and a
  dense-integer-key-inside-key penalty;
* schema: column/entity-name compatibility and SQL-type compatibility recovered from the official
  MusicBrainz CreateTables.sql.  SQL comments are deliberately ignored because many state FKs.

The current official schema may not have the same column order as the archived dump.  Names/types
are used only for tables whose current column count exactly matches the conversion specification;
coverage is printed so this limitation is visible.

usage:
  python experiments/report/musicbrainz_ranking_ablation.py \
      [--base $WORK_ROOT/musicbrainz-full] \
      [--schema $WORK_ROOT/musicbrainz-full/CreateTables-4ddcb78e.sql  (scripts/fetch-musicbrainz-schema.sh)]
"""
import argparse
import csv
import hashlib
import json
import math
import pickle
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths
sys.path.insert(0, str(paths.REPO / "profile"))
import genuineness  # noqa: F401  # needed when unpickling Background


CREATE_TABLE = re.compile(r"^CREATE TABLE\s+(?:musicbrainz\.)?([a-z_][a-z0-9_]*)\b")
COLUMN = re.compile(r"^\s{4}([a-z_][a-z0-9_]*)\s+([^,]+?)(?:,|$)")
POSITIONAL = re.compile(r"^column([0-9]+)$")
INTEGER = re.compile(r"^-?[0-9]+$")
STORE_PREFIX = re.compile(r"^(?:pg\.|mongo\.|neo4j:)")
GENERIC = {"id", "gid", "new", "column", "entity", "pk", "fk"}


def parse_schema(path):
    """Return {table: [(column, type-category)]}; ignore comments and FK declarations."""
    tables = {}
    current = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("--", 1)[0].rstrip()
        create = CREATE_TABLE.match(line)
        if create:
            current = create.group(1)
            tables[current] = []
            continue
        if current is None:
            continue
        if line.strip().startswith(");"):
            current = None
            continue
        column = COLUMN.match(line)
        if not column:
            continue
        name, declaration = column.groups()
        tables[current].append((name, type_category(declaration)))
    return tables


def type_category(declaration):
    declaration = declaration.upper().strip()
    # "INT" is bare in MusicBrainz's schema and must be matched before the prefix test, or it
    # falls through to "other" and every INT column looks untyped.
    if declaration in ("INT", "INT NOT NULL") or any(
            declaration.startswith(token) for token in
            ("SERIAL", "SMALLSERIAL", "BIGSERIAL", "INTEGER", "SMALLINT", "BIGINT", "INT ")):
        return "integer"
    if declaration.startswith(("NUMERIC", "DECIMAL", "REAL", "DOUBLE")):
        return "number"
    if declaration.startswith("UUID"):
        return "uuid"
    if declaration.startswith(("TIMESTAMP", "DATE", "TIME", "INTERVAL")):
        return "temporal"
    if declaration.startswith("BOOLEAN"):
        return "boolean"
    if declaration.startswith(("TEXT", "VARCHAR", "CHAR", "CITEXT")):
        return "text"
    return "other"


def source_table(relation):
    bare = STORE_PREFIX.sub("", relation)
    return bare.rsplit("@", 1)[-1]


def tokens(value):
    return {token for token in re.split(r"[^a-z0-9]+", value.lower())
            if token and token not in GENERIC}


def entity_tokens(table):
    value = table
    for suffix in ("_gid_redirect", "_alias_type", "_alias", "_type"):
        if value.endswith(suffix):
            value = value[:-len(suffix)]
            break
    if value.startswith("l_"):
        value = value[2:]
    return tokens(value)


def role_tokens(table, column):
    """Infer a column's entity role from names only, without FK comments or the oracle."""
    direct = tokens(column)
    if column.startswith("entity"):
        # MusicBrainz link-table names enumerate their participating entity kinds.  We do not use
        # the FK comments that say which entity position points where; both named kinds are evidence.
        return entity_tokens(table)
    if column in {"parent", "root"}:
        return entity_tokens(table)
    if column in {"new_id"} and table.endswith("_gid_redirect"):
        return entity_tokens(table)
    if column == "type":
        return entity_tokens(table) | {"type"}
    if column in {"link", "language", "script", "gender", "area", "artist", "recording",
                  "release", "release_group", "label", "place", "series", "url", "work",
                  "genre", "instrument", "medium", "cdtoc", "track", "format"}:
        return direct
    return direct


def compatibility(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / len(right)


def normalized_ranks(rows, feature):
    """Midranks in [0,1], with larger feature values better."""
    ordered = sorted(range(len(rows)), key=lambda index: feature(rows[index]))
    ranks = [0.0] * len(rows)
    start = 0
    while start < len(ordered):
        value = feature(rows[ordered[start]])
        end = start + 1
        while end < len(ordered) and feature(rows[ordered[end]]) == value:
            end += 1
        mid = (start + end - 1) / 2
        score = mid / max(len(rows) - 1, 1)
        for position in range(start, end):
            ranks[ordered[position]] = score
        start = end
    return ranks


def aggregate(rows, features):
    component_ranks = [normalized_ranks(rows, feature) for feature in features]
    order = sorted(range(len(rows)),
                   key=lambda index: (-sum(r[index] for r in component_ranks),
                                      -rows[index]["significance"],
                                      rows[index]["dependent"], rows[index]["referenced"]))
    return [rows[index] for index in order]


def average_precision(ranked, truth, recoverable):
    hits = 0
    total = 0.0
    for position, row in enumerate(ranked, 1):
        if (row["dependent"], row["referenced"]) in truth:
            hits += 1
            total += hits / position
    return total / max(recoverable, 1)


def metrics(ranked, truth, recoverable):
    marks = {}
    hits = 0
    for position, row in enumerate(ranked, 1):
        if (row["dependent"], row["referenced"]) in truth:
            hits += 1
            for target in (0.50, 0.75, 0.90, 1.00):
                needed = math.ceil(target * recoverable)
                if target not in marks and hits >= needed:
                    marks[target] = position
    precision_at = {}
    recall_at = {}
    for budget in (10, 25, 50, 100, 200, 500, 1000, 2000):
        kept = ranked[:budget]
        found = sum((row["dependent"], row["referenced"]) in truth for row in kept)
        precision_at[budget] = found / max(len(kept), 1)
        recall_at[budget] = found / max(recoverable, 1)
    return marks, precision_at, recall_at, average_precision(ranked, truth, recoverable)


CAVEAT = """
Important: the schema-name rules were developed while inspecting this corpus, including treating
"root" as a self-reference after auditing a missed key. These numbers are exploratory rather than
held-out evidence; experiments/report/name_rules_holdout.py evaluates the frozen rules on corpora
they were not built on."""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=paths.WORK_ROOT / "musicbrainz-full")
    parser.add_argument("--schema", type=Path,
                        default=paths.WORK_ROOT / "musicbrainz-full" /
                        "CreateTables-4ddcb78e.sql")
    parser.add_argument("--range-cache", type=Path,
                        default=paths.WORK_ROOT / "musicbrainz-full" / "integer-ranges.json")
    parser.add_argument("--no-range-scan", action="store_true",
                        help="use the sampled background when no exact range cache exists")
    args = parser.parse_args()
    if not args.schema.exists():
        raise SystemExit(
            f"schema not found: {args.schema}\n"
            "Download MusicBrainz CreateTables.sql at commit "
            "4ddcb78e4451d6569c54ff249a5da9926fdb6ebf or pass --schema."
        )

    scores_path = args.base / "genuineness-prefiltered.json"
    cache_path = args.base / "background.pkl"
    truth_document = json.loads((args.base / "ground_truth.json").read_text(encoding="utf-8"))
    truth = {(entry["dependent"], entry["referenced"])
             for entry in truth_document["dependencies"]}
    rows = json.loads(scores_path.read_text(encoding="utf-8"))
    with cache_path.open("rb") as handle:
        stats, background = pickle.load(handle)

    spec = json.loads((paths.REPO / "experiments" / "datasets" / "specs" /
                       "musicbrainz-full.json").read_text(encoding="utf-8"))
    schema = parse_schema(args.schema)
    mapped = {}
    for table, table_spec in spec["tables"].items():
        declared = schema.get(table)
        if declared and len(declared) == len(table_spec["columns"]):
            mapped[table] = declared

    def layout(attribute):
        stat = stats[attribute]
        relation = stat["relation"]
        table = source_table(relation)
        leaf = attribute[len(relation) + 1:].rsplit(".", 1)[-1]
        position = POSITIONAL.match(leaf)
        index = int(position.group(1)) - 1 if position else None
        name, category = leaf, "unknown"
        if index is not None and table in mapped and 0 <= index < len(mapped[table]):
            name, category = mapped[table][index]
        return stat, relation, table, leaf, index, name, category

    candidate_attributes = {row[side] for row in rows for side in ("dependent", "referenced")}
    ranges = {}
    if args.range_cache.exists():
        ranges = json.loads(args.range_cache.read_text(encoding="utf-8"))
    elif not args.no_range_scan:
        needed = defaultdict(lambda: defaultdict(list))
        for attribute in candidate_attributes:
            _, _, table, _, index, _, category = layout(attribute)
            if category == "integer" and index is not None:
                needed[table][index].append(attribute)
        source = paths.DATA_ROOT / "Musicbrainz"
        for table, positions in sorted(needed.items()):
            data_path = source / table
            if not data_path.exists():
                continue
            extrema = {index: [None, None] for index in positions}
            with data_path.open(encoding="utf-8", errors="replace", newline="") as handle:
                reader = csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
                for fields in reader:
                    for index in positions:
                        if index >= len(fields) or not INTEGER.match(fields[index]):
                            continue
                        value = int(fields[index])
                        low, high = extrema[index]
                        extrema[index] = [value if low is None else min(low, value),
                                          value if high is None else max(high, value)]
            for index, attributes in positions.items():
                if extrema[index][0] is not None:
                    for attribute in attributes:
                        ranges[attribute] = extrema[index]
        args.range_cache.parent.mkdir(parents=True, exist_ok=True)
        args.range_cache.write_text(json.dumps(ranges, indent=2, sort_keys=True), encoding="utf-8")

    sample_factor = (1 << 32) / max(background.limit, 1)

    def metadata(attribute):
        stat, relation, table, leaf, _, name, category = layout(attribute)
        sample = background.support.get(attribute, {})
        numeric = [int(value) for value in sample if INTEGER.match(value)]
        numeric_share = len(numeric) / max(len(sample), 1)
        density = 0.0
        range_source = "none"
        if attribute in ranges:
            low, high = ranges[attribute]
            density = min(1.0, stat["distinct"] / max(high - low + 1, 1))
            range_source = "exact"
        elif len(numeric) >= 16 and numeric_share >= 0.95:
            span = max(numeric) - min(numeric) + 1
            density = min(1.0, stat["distinct"] / max(span, 1))
            range_source = "sample"
        return {**stat, "attribute": attribute, "table": table, "name": name,
                "type": category, "density": density, "sampled": len(sample),
                "sample_factor": sample_factor, "range_source": range_source}

    meta_cache = {attribute: metadata(attribute) for attribute in stats}
    schema_candidates = 0
    for row in rows:
        left = meta_cache[row["dependent"]]
        right = meta_cache[row["referenced"]]
        left_role = role_tokens(left["table"], left["name"])
        right_entity = entity_tokens(right["table"])
        name_score = compatibility(left_role, right_entity)
        if left["table"] == right["table"] and left["name"] == "parent":
            name_score = 1.0
        type_score = (1.0 if left["type"] == right["type"] and left["type"] != "unknown"
                      else 0.5 if {left["type"], right["type"]} <= {"integer", "number"}
                      else 0.25 if "unknown" in {left["type"], right["type"]}
                      else 0.0)
        dependent_key = (left["rows"] > 0 and left["distinct"] == left["filled"] == left["rows"])
        utilization = left["distinct"] / max(right["distinct"], 1)
        presence = left["filled"] / max(left["rows"], 1)
        dense_key_inside_key = (dependent_key and left["type"] == "integer" and
                                right["type"] == "integer" and left["density"] >= 0.8 and
                                right["density"] >= 0.8 and name_score == 0.0)
        row.update({
            "dependent_schema_name": left["name"],
            "referenced_schema_name": right["name"],
            "dependent_source_table": left["table"],
            "referenced_source_table": right["table"],
            "name_score": name_score,
            "type_score": type_score,
            "utilization": min(utilization, 1.0),
            "presence": presence,
            "dependent_key": 1.0 if dependent_key else 0.0,
            "both_integer": 1.0 if left["type"] == right["type"] == "integer" else 0.0,
            "left_density": left["density"],
            "right_density": right["density"],
            "dependent_not_key": 0.0 if dependent_key else 1.0,
            "dense_key_inside_key": 1.0 if dense_key_inside_key else 0.0,
            "not_dense_key_inside_key": 0.0 if dense_key_inside_key else 1.0,
        })
        if left["type"] != "unknown" and right["type"] != "unknown":
            schema_candidates += 1

    recoverable = sum((row["dependent"], row["referenced"]) in truth for row in rows)
    sig = lambda row: row["significance"]
    name = lambda row: row["name_score"]
    variants = [
        ("significance", [sig]),
        ("schema name only", [name]),
        ("sig + schema name", [sig, name]),
        ("sig + dependent-key", [sig, lambda row: row["dependent_not_key"]]),
        ("sig + dense-key penalty", [sig, lambda row: row["not_dense_key_inside_key"]]),
        ("sig + utilization", [sig, lambda row: row["utilization"]]),
        ("sig + presence", [sig, lambda row: row["presence"]]),
        ("sig + SQL type", [sig, lambda row: row["type_score"]]),
        ("sig + name + key", [sig, name, lambda row: row["dependent_not_key"]]),
        ("sig + all proposed", [sig, name, lambda row: row["dependent_not_key"],
                                lambda row: row["not_dense_key_inside_key"],
                                lambda row: row["utilization"], lambda row: row["presence"],
                                lambda row: row["type_score"]]),
    ]

    print("MusicBrainz ranking-feature ablation")
    print(f"  candidates: {len(rows):,}; recoverable oracle keys: {recoverable}/{len(truth)}")
    print(f"  official-schema mapping: {len(mapped)}/{len(spec['tables'])} source tables; "
          f"both sides named/typed for {schema_candidates:,}/{len(rows):,} candidates")
    if args.schema.exists():
        digest = hashlib.sha256(args.schema.read_bytes()).hexdigest()
        print(f"  schema: {args.schema} (sha256 {digest[:16]})")
    print(f"  numeric-density sampling factor: 1 in {sample_factor:.0f}\n")
    print(f"  exact integer ranges: {len(ranges):,} candidate attributes "
          f"(cache {args.range_cache})\n")

    print(f"{'ranking':<25} {'AP':>7} {'P@25':>7} {'P@50':>7} {'R@200':>7} "
          f"{'R@500':>7} {'50% at':>8} {'75% at':>8} {'90% at':>8} {'100% at':>8}")
    print("-" * 103)
    evaluated = []
    for name, features in variants:
        ranked = aggregate(rows, features)
        marks, precision_at, recall_at, ap = metrics(ranked, truth, recoverable)
        evaluated.append((name, ranked, marks, precision_at, recall_at, ap))
        print(f"{name:<25} {ap:>7.3f} {precision_at[25]:>7.1%} {precision_at[50]:>7.1%} "
              f"{recall_at[200]:>7.1%} {recall_at[500]:>7.1%} "
              f"{marks.get(.5, 0):>8,} {marks.get(.75, 0):>8,} "
              f"{marks.get(.9, 0):>8,} {marks.get(1.0, 0):>8,}")

    print("\nfeature audit (oracle used only below this line)")
    for feature in ("dependent_not_key", "not_dense_key_inside_key", "name_score", "type_score"):
        positives = [row[feature] for row in rows
                     if (row["dependent"], row["referenced"]) in truth]
        others = [row[feature] for row in rows
                  if (row["dependent"], row["referenced"]) not in truth]
        print(f"  {feature:<26} oracle mean {sum(positives)/max(len(positives),1):.3f}; "
              f"unlabelled mean {sum(others)/max(len(others),1):.3f}")
    dense_attributes = sum(meta["density"] >= 0.8 for meta in meta_cache.values())
    dense_key_candidates = sum(row["dense_key_inside_key"] > 0 for row in rows)
    print(f"  dense numeric attributes     {dense_attributes}/{len(meta_cache)}; "
          f"dense key-inside-key candidates {dense_key_candidates}/{len(rows)}")
    print(f"  detector intersections      dependent key {sum(r['dependent_key'] for r in rows):.0f}; "
          f"both integer {sum(r['both_integer'] for r in rows):.0f}; "
          f"left dense {sum(r['left_density'] >= .8 for r in rows)}; "
          f"right dense {sum(r['right_density'] >= .8 for r in rows)}; "
          f"name zero {sum(r['name_score'] == 0 for r in rows)}")

    print("\nname-compatibility selection (exploratory; no oracle-fitted threshold)")
    for label, predicate in (("> 0", lambda value: value > 0),
                             (">= 0.5", lambda value: value >= 0.5),
                             ("= 1", lambda value: value == 1.0)):
        kept = [row for row in rows if predicate(row["name_score"])]
        hits = sum((row["dependent"], row["referenced"]) in truth for row in kept)
        print(f"  name score {label:<6}: kept {len(kept):>5,}; "
              f"recall {hits:>3}/{recoverable}; oracle precision {hits/max(len(kept),1):>6.1%}")
    missed_by_names = [row for row in rows
                       if (row["dependent"], row["referenced"]) in truth and
                       row["name_score"] == 0]
    if missed_by_names:
        print("  labelled keys with no name evidence:")
        for row in missed_by_names:
            print(f"    {row['dependent']} ({row['dependent_schema_name']}) -> "
                  f"{row['referenced']} ({row['referenced_source_table']}."
                  f"{row['referenced_schema_name']})")

    best = max(evaluated, key=lambda item: item[-1])
    print(f"\nbest by average precision: {best[0]} ({best[-1]:.3f})")
    print(CAVEAT)


if __name__ == "__main__":
    main()
