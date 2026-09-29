"""Emits the Heterogeneity Profile as a set of records rather than as a vector of rates.

Each record is one heterogeneity finding with fields kind, subject, cell (the quadrant of the
heterogeneity matrix, from whether it crosses stores and whether it concerns names or values;
preconditions have none), evidence, measurement (a named value) and, where available, genuineness
and salience. Evidence and measurement are typed by kind. Record ids are stable, so two profiles
of one instance differ by a set difference, and the printed aggregate is a fold over the records.

usage: hp_records.py <exportDir> <indResultDir> [--rho=1.0] [--similarity=<dir>]
                     [--genuineness=<json>] [--intermodel=<json>] [--u=<float>] [--transforms=<json>]
                     [--jsonl=<out>] [--top=20]
"""
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from name_similarity import WordNet, normalised, syntactic_score  # noqa: E402
from metanome_dumps import read_beside  # noqa: E402
from genuineness import gpdep  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments' / 'lib'))
import entailed  # noqa: E402

TRAILING_COMMA = re.compile(r",\s*([}\]])")
STRING_VARIANT, LEXICAL = 0.7, 0.7

# Matrix cell by (crosses stores?, names or values?).
CELL = {("intra", "schema"): "A.13", ("intra", "data"): "A.14",
        ("inter", "schema"): "A.23", ("inter", "data"): "A.24"}


def flag(name, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


# ------------------------------------------------------------------------------------- the inputs

def read_export(export):
    """Per observation set: columns, distinct values, non-null counts, store, scope."""
    csv.field_size_limit(10_000_000)
    sets = {}
    for entry in json.loads((export / "catalog.json").read_text(encoding="utf-8")):
        with (export / entry["file"]).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            values = {column: set() for column in columns}
            filled = dict.fromkeys(columns, 0)
            rows = 0
            for row in reader:
                rows += 1
                for column in columns:
                    if row.get(column):
                        values[column].add(row[column])
                        filled[column] += 1
        sets[entry["relation"]] = {
            "columns": columns, "values": values, "filled": filled, "rows": rows,
            "scope": entry.get("scope", "base"), "file": entry["file"],
            "path": export / entry["file"],
            "store": entry.get("store", store_from_name(entry["relation"]))}
    return sets


def store_from_name(relation):
    base = relation.split("@")[0]
    if base.startswith("neo4j:"):
        return "graph"
    if base.startswith(("unibench.", "shop.", "mongo.")):
        return "document"
    return "relational"


def read_inds(result_dir):
    links = []
    for path in sorted(Path(result_dir).glob("*ary_pINDs.json")):
        text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
        for relation in json.loads(text):
            for pind in relation.get("pINDs", []):
                dependent = ".".join(pind["dependant"])
                for ref in pind["referenced"]:
                    links.append((relation["relation"], dependent, ref["relation"],
                                  ".".join(ref["attribute"]), ref.get("partialDegree", 1.0)))
    return links


# ------------------------------------------------------------------------------------ the records

def identity(kind, subject, evidence):
    """A stable record id, so two profiles of one instance differ by a set difference.

    Excludes measurements and scores, so a finding whose coverage changes keeps its id.
    """
    material = json.dumps([kind, subject, evidence], sort_keys=True)
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


def record(kind, model_scope, aspect, subject, evidence, strength=None, measurement=None,
           **extra):
    """One record.

    `measurement` is a (field, value) pair naming what the number is (coverage, presence,
    similarity, ...). `strength` is its numeric alias used for sorting; it is None for kinds that
    measure nothing or whose measurement is categorical.
    """
    cell = CELL.get((model_scope, aspect))
    field, value = measurement if measurement else (None, strength)
    numeric = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return {"id": identity(kind, subject, evidence), "cell": cell, "kind": kind,
            "model_scope": model_scope, "aspect": aspect, "subject": subject,
            "evidence": evidence,
            "measurement": {"field": field, "value": value} if field else None,
            "strength": numeric, **extra}


def set_coverage(sets, dependent_set, dependent, referenced_set, referenced):
    """Coverage over supp(L), or None when the dependent observation is empty.

    An empty observation is contained in every other, so the inclusion is vacuous rather than a
    dependency at coverage zero.
    """
    left = sets[dependent_set]["values"].get(dependent, set())
    right = sets[referenced_set]["values"].get(referenced, set())
    if not left:
        return None
    return len(left & right) / len(left)


def link_records(sets, links, similarity_links, rho, wordnet, entailed_pairs=frozenset()):
    """One or more records per discovered link, plus the vacuous ones counted separately."""
    records, vacuous = [], 0
    equality = {(a, b, c, d) for a, b, c, d, _ in links}

    for dependent_set, dependent, referenced_set, referenced, degree in links:
        if dependent_set not in sets or referenced_set not in sets:
            continue
        # Skip attributes missing from the export (the result directory may be stale).
        if (dependent not in sets[dependent_set]["values"]
                or referenced not in sets[referenced_set]["values"]):
            continue
        # A union scope contains its members by construction; such inclusions say nothing about
        # the data.
        if (f"{dependent_set}.{dependent}", f"{referenced_set}.{referenced}") in entailed_pairs:
            continue
        coverage = set_coverage(sets, dependent_set, dependent, referenced_set, referenced)
        if coverage is None:
            vacuous += 1
            continue
        crossing = ("inter" if sets[dependent_set]["store"] != sets[referenced_set]["store"]
                    else "intra")
        subject = {"dependent": f"{dependent_set}.{dependent}",
                   "referenced": f"{referenced_set}.{referenced}",
                   "stores": [sets[dependent_set]["store"], sets[referenced_set]["store"]]}
        # Dependent/referenced domain size. Recorded but not used for ranking: equal-sized shared
        # domains (e.g. two `country` columns) score 1.0 without being references.
        referenced_domain = len(sets[referenced_set]["values"].get(referenced, ()))
        dependent_domain = len(sets[dependent_set]["values"].get(dependent, ()))
        domain_ratio = (dependent_domain / referenced_domain) if referenced_domain else None
        evidence = {"dependency": "pIND", "arity": 1, "comparison": "equality", "rho": rho,
                    "domain_ratio": domain_ratio}

        # Schema aspect: linked but not named alike. A spelling variant is checked before a
        # synonym so that it is not reported as one.
        left, right = normalised(dependent), normalised(referenced)
        if left != right:
            syntactic = syntactic_score(left, right)
            semantic = wordnet.name_similarity(dependent, referenced)
            naming = ("string-variant" if syntactic >= STRING_VARIANT else
                      "lexical-semantic" if semantic >= LEXICAL else "unrelated")
            records.append(record("renamed-link", crossing, "schema", subject, evidence,
                                  measurement=("name-similarity", max(syntactic, semantic)),
                                  naming=naming))

        # One side is a nested scope and the other is not. Nested (array/list) scopes are named
        # entity@path by the export; the scope name alone does not identify nesting across stores.
        if ("@" in dependent_set) != ("@" in referenced_set):
            records.append(record("layout-variant", crossing, "schema", subject, evidence,
                                  scopes=[sets[dependent_set]["scope"],
                                          sets[referenced_set]["scope"]]))

        if coverage < 1.0:
            records.append(record("partial-link", crossing, "data", subject, evidence,
                                  measurement=("coverage-set", coverage),
                                  reported_degree=degree))

        # A link into a key is a precondition (for a join-induced scope), not a finding.
        referenced_values = sets[referenced_set]["values"].get(referenced, set())
        rows = sets[referenced_set]["rows"]
        if rows and len(referenced_values) == rows == sets[referenced_set]["filled"][referenced]:
            records.append(record("join-candidate", crossing, None, subject,
                                  {**evidence, "dependency": "IND+UCC"},
                                  measurement=("coverage-set", coverage)))

    for dependent_set, dependent, referenced_set, referenced, degree in similarity_links:
        key = (dependent_set, dependent, referenced_set, referenced)
        if key in equality or dependent_set not in sets or referenced_set not in sets:
            continue
        crossing = ("inter" if sets[dependent_set]["store"] != sets[referenced_set]["store"]
                    else "intra")
        records.append(record(
            "similarity-only-link", crossing, "data",
            {"dependent": f"{dependent_set}.{dependent}",
             "referenced": f"{referenced_set}.{referenced}",
             "stores": [sets[dependent_set]["store"], sets[referenced_set]["store"]]},
            {"dependency": "psIND", "arity": 1, "comparison": "edit-distance", "rho": rho},
            measurement=("coverage-bag", degree)))
    return records, vacuous


def scope_records(sets):
    """Findings about one scope rather than a link.

    optional-extractor: an extractor present on only part of its scope's matches (a coexisting
    schema variant); evidence is its presence share.
    """
    records = []
    for relation, data in sets.items():
        for column in data["columns"]:
            filled, rows = data["filled"][column], data["rows"]
            if rows and 0 < filled < rows:
                records.append(record(
                    "optional-extractor", "intra", "schema",
                    {"extractor": f"{relation}.{column}", "stores": [data["store"]]},
                    {"dependency": "presence", "scope": data["scope"]},
                    measurement=("presence", filled / rows)))
    return records


# ------------------------------------------------------------- records the other two classes give

def comparison_domain(values):
    """The domain ("numeric" or "text") a support agrees on, or None when it mixes.

    Only two domains, matching what the discovery core distinguishes when pruning candidates.
    """
    kinds = set()
    for value in values:
        try:
            float(value)
            kinds.add("numeric")
        except ValueError:
            kinds.add("text")
        if len(kinds) > 1:
            return None
    return kinds.pop() if kinds else None


# Stores name the same type differently (bigint, Long, INTEGER), so declared types are compared by
# category.
TYPE_CATEGORY = {
    "bigint": "integer", "integer": "integer", "smallint": "integer", "int": "integer",
    "Integer": "integer", "Long": "integer", "INTEGER": "integer", "Int64": "integer",
    "double precision": "decimal", "numeric": "decimal", "real": "decimal", "Double": "decimal",
    "FLOAT": "decimal", "float": "decimal",
    "text": "text", "character varying": "text", "character": "text", "String": "text",
    "STRING": "text", "str": "text", "varchar": "text",
    "boolean": "boolean", "Boolean": "boolean", "BOOLEAN": "boolean",
    "date": "temporal", "timestamp": "temporal", "timestamp without time zone": "temporal",
    "DATE": "temporal", "DATE_TIME": "temporal", "Date": "temporal",
    "ObjectId": "opaque-identifier", "Binary": "opaque-identifier", "uuid": "opaque-identifier",
    "BYTE_ARRAY": "opaque-identifier",
}


def read_types(export):
    """{observation set -> {extractor -> {declared type -> count}}}, or {} when absent.

    Read from types.json beside catalog.json; without it, type-based findings are skipped.
    """
    path = Path(export) / "types.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def declared_type(types, relation, column):
    """The type a store declared most often for one extractor, and its category."""
    histogram = types.get(relation, {}).get(column) or {}
    if not histogram:
        return None, None
    declared = max(histogram, key=histogram.get)
    return declared, TYPE_CATEGORY.get(declared, declared)


def type_records(sets, links, types):
    """type-variant: a link whose two sides have different declared type categories.

    The schema-level counterpart of domain-variant, based on the stores' declared types rather
    than the serialized values.
    """
    if not types:
        return []
    records = []
    for dependent_set, dependent, referenced_set, referenced, _ in links:
        if dependent_set not in sets or referenced_set not in sets:
            continue
        left_declared, left_kind = declared_type(types, dependent_set, dependent)
        right_declared, right_kind = declared_type(types, referenced_set, referenced)
        if not left_kind or not right_kind or left_kind == right_kind:
            continue
        crossing = ("inter" if sets[dependent_set]["store"] != sets[referenced_set]["store"]
                    else "intra")
        records.append(record(
            "type-variant", crossing, "schema",
            {"dependent": f"{dependent_set}.{dependent}",
             "referenced": f"{referenced_set}.{referenced}",
             "stores": [sets[dependent_set]["store"], sets[referenced_set]["store"]]},
            {"dependency": "IND + declared types",
             "declared": [left_declared, right_declared]},
            measurement=("type-categories", f"{left_kind} vs {right_kind}")))
    return records


def domain_records(sets, links):
    """domain-variant: a link whose two sides carry different comparison domains.

    Values are compared as strings, so a numeric extractor can be included in a textual one; the
    same information is then written as a number in one store and as text in the other.
    """
    records = []
    cache = {}

    def domain(relation, column):
        if (relation, column) not in cache:
            cache[(relation, column)] = comparison_domain(sets[relation]["values"].get(column, ()))
        return cache[(relation, column)]

    for dependent_set, dependent, referenced_set, referenced, _ in links:
        if dependent_set not in sets or referenced_set not in sets:
            continue
        if (dependent not in sets[dependent_set]["values"]
                or referenced not in sets[referenced_set]["values"]):
            continue
        left, right = domain(dependent_set, dependent), domain(referenced_set, referenced)
        if left is None or right is None or left == right:
            continue
        crossing = ("inter" if sets[dependent_set]["store"] != sets[referenced_set]["store"]
                    else "intra")
        records.append(record(
            "domain-variant", crossing, "data",
            {"dependent": f"{dependent_set}.{dependent}",
             "referenced": f"{referenced_set}.{referenced}",
             "stores": [sets[dependent_set]["store"], sets[referenced_set]["store"]]},
            {"dependency": "pIND", "comparison": "equality", "domains": [left, right]},
            domains=[left, right]))
    return records


def multiplicity_records(sets, links, entailed_pairs=frozenset()):
    """multiplicity-variant: a link whose dependent side is also unique (one-to-one).

    Such a link splits one entity across two scopes rather than expressing a relationship.
    """
    records = []
    for dependent_set, dependent, referenced_set, referenced, _ in links:
        if dependent_set not in sets or referenced_set not in sets:
            continue
        if (f"{dependent_set}.{dependent}", f"{referenced_set}.{referenced}") in entailed_pairs:
            continue
        left = sets[dependent_set]
        if dependent not in left["values"]:
            continue
        support = left["values"][dependent]
        if not support or not left["rows"]:
            continue
        crossing = ("inter" if left["store"] != sets[referenced_set]["store"] else "intra")
        one_to_one = len(support) == left["rows"] == left["filled"][dependent]
        # Only the one-to-one case is a finding; a repeating reference is the normal case.
        if not one_to_one:
            continue
        records.append(record(
            "multiplicity-variant", crossing, "schema",
            {"dependent": f"{dependent_set}.{dependent}",
             "referenced": f"{referenced_set}.{referenced}",
             "stores": [left["store"], sets[referenced_set]["store"]]},
            {"dependency": "pIND", "shape": "one-to-one" if one_to_one else "one-to-many"},
            measurement=("distinct-share", len(support) / left["rows"]),
            shape="one-to-one" if one_to_one else "one-to-many"))
    return records


def key_records(sets, uccs):
    """candidate-key (a unary UCC, a precondition) and key-loss records.

    key-loss: a nested scope has no unary key although its parent scope has one, because exploding
    an array repeats the parent's key per element. Evidence is the two scopes' UCCs compared.
    """
    records = []
    arity = {}
    for relation, keys in uccs.items():
        if relation not in sets:
            continue
        arity[relation] = min((len(k) for k in keys), default=None)
        for key in keys:
            if len(key) != 1:
                continue
            records.append(record(
                "candidate-key", "intra", None,
                {"extractor": f"{relation}.{key[0]}", "stores": [sets[relation]["store"]]},
                {"dependency": "UCC", "arity": 1, "scope": sets[relation]["scope"]}))

    for relation, minimal in arity.items():
        base = relation.split("@")[0]
        if base == relation or base not in arity:
            continue
        parent = arity[base]
        if parent == 1 and (minimal is None or minimal > 1):
            records.append(record(
                "key-loss", "intra", "schema",
                {"extractor": relation, "stores": [sets[relation]["store"]]},
                {"dependency": "UCC-comparison", "scope": sets[relation]["scope"],
                 "compared_with": base},
                minimal_arity=minimal, parent_arity=parent))
    return records


def derivable_records(sets, fds, links, uccs, rows_of=None):
    """derivable-extractor: an extractor determined by others in the same scope (an intra-model FD).

    FDs whose determinant is a key are skipped, and nearly unique determinants are filtered by
    requiring gpdep > 0 (when rows_of is given). FDs whose extractors are jointly present on fewer
    than two matches are counted as vacuous: the export writes a null string where an extractor is
    undefined, so such an FD holds only on agreeing nulls. Presence bounds joint support from above.
    Returns (records, vacuous count).
    """
    records, vacuous = [], 0
    for relation, rules in fds.items():
        if relation not in sets:
            continue
        keys = {frozenset(k) for k in uccs.get(relation, ())}
        data = sets[relation]

        def trivial(columns):
            """Whether the determinant is a key: a known UCC, or containing a unique extractor
            (which also works when no UCC dump is available)."""
            if frozenset(columns) in keys:
                return True
            return any(data["rows"] and len(data["values"].get(c, ())) == data["rows"]
                       == data["filled"].get(c, -1) for c in columns)

        for determinant, dependent, degree in rules:
            target = f"{relation}.{dependent}"
            if not determinant or trivial(determinant):
                continue
            if min((data["filled"].get(c, 0) for c in (*determinant, dependent)),
                   default=0) < 2:
                vacuous += 1
                continue
            score = None
            if rows_of is not None:
                score = fd_gpdep(rows_of(relation), determinant, dependent)
                if score is None or score <= 0:
                    continue
            records.append(record(
                "derivable-extractor", "intra", "schema",
                {"extractor": target,
                 "determinant": [f"{relation}.{c}" for c in determinant],
                 "stores": [sets[relation]["store"]]},
                {"dependency": "FD", "theta": degree, "scope": sets[relation]["scope"]},
                measurement=("theta", degree), gpdep=score))
    return records, vacuous


def fd_gpdep(rows, determinant, dependent):
    """gpdep for one FD over already-loaded rows, or None when it cannot be computed."""
    if not rows or dependent not in rows[0]:
        return None
    if any(c not in rows[0] for c in determinant):
        return None
    groups = {}
    right = {}
    for row in rows:
        key = tuple(row[c] for c in determinant)
        inner = groups.setdefault(key, {})
        inner[row[dependent]] = inner.get(row[dependent], 0) + 1
        right[row[dependent]] = right.get(row[dependent], 0) + 1
    _, _, score = gpdep([sum(v.values()) for v in groups.values()],
                        [list(v.values()) for v in groups.values()],
                        list(right.values()), len(rows))
    return score


def transform_records(sets, path):
    """transformed-link: an inclusion that holds under an exact value mapping (a prefix, padding,
    case, a unit), as found by discover_transforms.py; evidence names the transformation."""
    out = []
    for finding in json.loads(Path(path).read_text(encoding="utf-8")):
        dep = split_qualified(finding["dependent"], sets)
        ref = split_qualified(finding["referenced"], sets)
        if dep[0] is None or ref[0] is None:
            continue
        crossing = "inter" if sets[dep[0]]["store"] != sets[ref[0]]["store"] else "intra"
        out.append(record(
            "transformed-link", crossing, "data",
            {"dependent": finding["dependent"], "referenced": finding["referenced"],
             "stores": [sets[dep[0]]["store"], sets[ref[0]]["store"]]},
            {"dependency": "tIND", "arity": 1, "transformation": finding["transform"]},
            measurement=("coverage-set", finding["coverage"])))
    return out


def split_qualified(name, sets):
    """Split "<set>.<attribute>" using the known set names, longest match first.

    Both halves can contain dots -- "shop.orders@lineitems" is a set and
    "lineitems.l_linenumber" an attribute -- so the split cannot be positional.
    """
    for candidate in sorted(sets, key=len, reverse=True):
        if name.startswith(candidate + "."):
            return candidate, name[len(candidate) + 1:]
    return None, name


def intermodel_records(sets, path, floor=None):
    """redundant-extractor: an inter-model FD measured over a join-induced scope.

    One record per set of extractors, since an equality between two stores surfaces as several
    directed FDs. The join predicate is kept in `via` rather than in the evidence so that the
    record id does not depend on which join revealed it.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    floor = data.get("u", 0.0) if floor is None else floor
    groups = {}

    for finding in data["findings"]:
        if finding["utilisation"] < floor:
            continue
        left, right = (finding["predicate"].split(" -> ") + [""])[:2]
        scope = {"L": split_qualified(left, sets)[0], "R": split_qualified(right, sets)[0]}

        def qualify(column):
            owner = scope.get(column[:1])
            return f"{owner}.{column[2:]}" if owner else column

        determinant_text, determined = finding["fd"].split(" -> ")
        determinant = tuple(sorted(qualify(t.strip())
                                   for t in determinant_text.split(",") if t.strip()))
        determined = qualify(determined.strip())
        if not determinant:
            continue

        key = frozenset(determinant) | {determined}
        entry = groups.setdefault(key, {"directions": set(), "via": set(),
                                        "gpdep": 0.0, "participation": 1.0,
                                        "utilisation": 1.0})
        entry["directions"].add((determinant, determined))
        entry["via"].add(finding["predicate"])
        entry["gpdep"] = max(entry["gpdep"], finding["gpdep"])
        entry["participation"] = min(entry["participation"], finding["participation"])
        entry["utilisation"] = min(entry["utilisation"], finding["utilisation"])

    records = []
    for extractors, entry in groups.items():
        members = sorted(extractors)
        stores = sorted({sets[s]["store"] for s in
                         (split_qualified(c, sets)[0] for c in members) if s in sets})
        # Symmetric: both stores hold the same fact rather than one deriving the other.
        symmetric = len(entry["directions"]) > 1
        subject = {"extractors": members, "stores": stores}
        if not symmetric:
            determinant, determined = next(iter(entry["directions"]))
            subject = {"extractors": members, "stores": stores,
                       "determinant": list(determinant), "determined": determined}
        records.append(record(
            "redundant-extractor", "inter", "schema", subject,
            {"dependency": "inter-model FD", "symmetric": symmetric},
            measurement=("gpdep", entry["gpdep"]),
            via=sorted(entry["via"]),
            participation=entry["participation"],
            utilisation=entry["utilisation"]))
    return records



def path_depth(qualified, sets):
    """How deeply nested an extractor is: the number of path segments in its attribute name.

    Nested columns carry their full path from the entity root, e.g. `order_item.vendor.brand` is 3.
    """
    for relation in sorted(sets, key=len, reverse=True):
        if qualified.startswith(relation + "."):
            return len(qualified[len(relation) + 1:].split("."))
    return 1


def record_interestingness(records, sets):
    """Adds `salience`: how far each finding departs from this instance's own baseline.

    Salience says whether a finding is notable; `genuineness` says whether the underlying link is
    real. It is None for preconditions (candidate-key, join-candidate), key-loss (deterministic),
    multiplicity-variant (always one-to-one) and type-variant (categorical).
    """
    def measured(kind, field):
        values = [r["measurement"]["value"] for r in records
                  if r["kind"] == kind and r.get("measurement")
                  and r["measurement"]["field"] == field
                  and isinstance(r["measurement"]["value"], (int, float))]
        return sum(values) / len(values) if values else None

    # Baselines: mean name similarity of renamed links, and mean presence per scope.
    typical_name = measured("renamed-link", "name-similarity")
    scope_presence = {}
    for name, data in sets.items():
        rows = data["rows"]
        if not rows or not data["columns"]:
            continue
        shares = [data["filled"].get(c, 0) / rows for c in data["columns"]]
        scope_presence[name] = sum(shares) / len(shares)

    for entry in records:
        kind, measurement = entry["kind"], entry.get("measurement")
        value = measurement["value"] if measurement else None
        salience = None
        if kind == "renamed-link" and typical_name and isinstance(value, (int, float)):
            salience = max(0.0, (typical_name - value) / typical_name)
        elif kind == "optional-extractor" and isinstance(value, (int, float)):
            extractor = entry["subject"].get("extractor", "")
            relation = next((s for s in sets if extractor.startswith(s + ".")), None)
            baseline = scope_presence.get(relation)
            if baseline:
                salience = max(0.0, (baseline - value) / baseline)
        elif kind in ("partial-link", "similarity-only-link") and isinstance(value, (int, float)):
            salience = max(0.0, 1.0 - value)
        elif kind == "derivable-extractor":
            salience = entry.get("gpdep")
        elif kind == "redundant-extractor":
            # This kind reports gpdep as its measurement rather than beside it.
            salience = value if isinstance(value, (int, float)) else None
        elif kind == "layout-variant":
            # Magnitude is the relative difference in path depth.
            left = entry["subject"].get("dependent", "")
            right = entry["subject"].get("referenced", "")
            depths = [path_depth(name, sets) for name in (left, right)]
            deeper = max(depths)
            salience = (abs(depths[0] - depths[1]) / deeper) if deeper else None
        entry["salience"] = salience
    return records


def attach_genuineness(records, path):
    """Adds the chance-corrected score of the inclusion each link record rests on."""
    scored = {(s["dependent"], s["referenced"]): s
              for s in json.loads(Path(path).read_text(encoding="utf-8"))}
    for entry in records:
        subject = entry["subject"]
        match = scored.get((subject.get("dependent"), subject.get("referenced")))
        if match:
            entry["genuineness"] = match["significance"]
            entry["gcov"] = match["gcov"]
            entry["background_share"] = match["share"]
    return records


# ---------------------------------------------------------------------------------- the aggregate

def aggregate(records, vacuous):
    """The fold that recovers Table 1 from the record set."""
    links = {r["subject"].get("dependent") for r in records if "dependent" in r["subject"]}
    counts = {}
    for entry in records:
        counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
        if entry["cell"]:
            counts[entry["cell"]] = counts.get(entry["cell"], 0) + 1
    counts["vacuous"] = vacuous
    counts["distinct dependents"] = len(links)
    return counts


def main():
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    export, results = Path(positional[0]), Path(positional[1])
    sets = read_export(export)
    links = read_inds(results)
    similarity = read_inds(Path(flag("similarity"))) if flag("similarity") else []
    rho = float(flag("rho", 1.0))
    wordnet = WordNet()

    fds, uccs = read_beside(export, sets)
    types = read_types(export)

    loaded = {}

    def rows_of(relation):
        """Rows of one observation set, loaded once. Only the scopes with a non-trivial FD are
        read, so an instance whose FD dumps are absent never touches the CSVs at all."""
        if relation not in loaded:
            with sets[relation]["path"].open(encoding="utf-8", newline="") as handle:
                loaded[relation] = list(csv.DictReader(handle))
        return loaded[relation]

    entailed_pairs = entailed.entailed_pairs(export)
    records, vacuous = link_records(sets, links, similarity, rho, wordnet, entailed_pairs)
    records += scope_records(sets)
    records += domain_records(sets, links)
    records += type_records(sets, links, types)
    records += multiplicity_records(sets, links, entailed_pairs)
    records += key_records(sets, uccs)
    derivable, vacuous_fds = derivable_records(sets, fds, links, uccs, rows_of=rows_of)
    records += derivable
    if flag("intermodel"):
        records += intermodel_records(sets, flag("intermodel"),
                                      float(flag("u")) if flag("u") else None)
    if flag("transforms"):
        records += transform_records(sets, flag("transforms"))
    if flag("genuineness"):
        attach_genuineness(records, flag("genuineness"))
    record_interestingness(records, sets)
    records.sort(key=lambda r: (-r.get("genuineness", -1),
                                -(r.get("strength") if r.get("strength") is not None else -1)))

    provenance = {"export": str(export), "results": str(results), "rho": rho}
    for entry in records:
        entry["provenance"] = provenance

    excluded = f"{vacuous} vacuous inclusions"
    if vacuous_fds:
        excluded += f" and {vacuous_fds} unsupported FDs"
    print(f"Heterogeneity Profile: {len(records)} records over {len(sets)} observation sets "
          f"({excluded} excluded)")
    print(f"  from {len(links)} inclusions, {sum(len(v) for v in fds.values())} FDs and "
          f"{sum(len(v) for v in uccs.values())} UCCs\n")
    print(f"  {'cell':<6} {'kind':<22} {'records':>8}")
    counts = aggregate(records, vacuous)
    for kind in sorted(k for k in counts if k not in ("vacuous", "distinct dependents")
                       and not k.startswith("A.")):
        cells = sorted({r["cell"] or "-" for r in records if r["kind"] == kind})
        print(f"  {'/'.join(cells):<6} {kind:<22} {counts[kind]:>8}")
    print(f"\n  by quadrant: "
          + ", ".join(f"{c} {counts.get(c, 0)}" for c in ("A.13", "A.14", "A.23", "A.24")))

    # Rates use the population each kind is drawn from: links, extractors or scopes.
    population = {"link": len(links), "extractor": sum(len(s["columns"]) for s in sets.values()),
                  "scope": len(sets)}
    per_kind = {"renamed-link": "link", "layout-variant": "link", "multiplicity-variant": "link",
                "partial-link": "link", "similarity-only-link": "link", "domain-variant": "link",
                "type-variant": "link", "join-candidate": "link",
                "optional-extractor": "extractor", "candidate-key": "extractor",
                "key-loss": "scope", "derivable-extractor": "extractor",
                "redundant-extractor": "link", "transformed-link": "link"}
    print(f"\n  {'kind':<22} {'count':>7} {'per':<10} {'rate':>7}")
    for kind in sorted(k for k in counts if k in per_kind):
        unit = per_kind[kind]
        total = population[unit]
        rate = counts[kind] / total if total else 0.0
        print(f"  {kind:<22} {counts[kind]:>7} {unit:<10} {rate:>7.3f}")
    print(f"  populations: {population['link']} links, {population['extractor']} extractors, "
          f"{population['scope']} scopes")

    top = int(flag("top", 20))
    if top:
        print(f"\n  the {top} records a reader should look at first:")
        print(f"  {'genuine':>10} {'strength':>9} {'cell':>6} {'kind':<22} subject")
        for entry in records[:top]:
            subject = entry["subject"]
            if "dependent" in subject:
                name = f"{subject['dependent']} -> {subject['referenced']}"
            elif "extractor" in subject:
                name = subject["extractor"]
            else:
                name = " == ".join(subject.get("extractors", ["?"]))
            # Scores that do not apply to a kind print as a dash.
            score = entry.get("genuineness")
            value = entry.get("strength")
            print(f"  {f'{score:.1f}' if score is not None else '-':>10} "
                  f"{f'{value:.4f}' if value is not None else '-':>9} "
                  f"{entry['cell'] or '-':>6} {entry['kind']:<22} {name}")

    out = flag("jsonl")
    if out:
        with Path(out).open("w", encoding="utf-8") as handle:
            for entry in records:
                handle.write(json.dumps(entry) + "\n")
        print(f"\n  {len(records)} records written to {out}")


if __name__ == "__main__":
    main()
