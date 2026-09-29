"""Generates a multi-model conversion spec for MusicBrainz from its own foreign-key metadata.

Writing 150 table declarations by hand would be transcription, and transcription is where oracles
acquire errors. The corpus ships a foreign-key list; this derives the spec from it, so the oracle
and the instance cannot drift apart.

Three decisions are encoded here, and each is a claim the reader can check.

*Which tables.* Whole tables only, up to a size cap. Sampling a schema with hundreds of foreign
keys cannot be made referentially closed by any simple rule, and an unclosed sample turns exact
dependencies into partial ones for reasons that belong to the sampling rather than to the data --
the failure already recorded for IMDB's knownForTitles. Taking a table entirely avoids the question.

*Which store.* MusicBrainz names its tables by role, so the assignment can follow the schema's own
vocabulary rather than our preference:

    l_<a>_<b>                     relationship tables, i.e. edges          -> graph
    <e>_alias / _annotation /     multi-valued satellites of one entity,
    _tag / _ipi / _isni /         which is what a document embeds          -> document
    _gid_redirect
    everything else                                                        -> relational

*Column names.* Positional, column1..columnN, which is exactly how the shipped foreign-key list
names them. Using the documented schema names instead would mean asserting an alignment nothing in
the dump verifies.

usage: gen_musicbrainz_spec.py [--cap-mb=200] [--out=specs/musicbrainz-full.json] [--nest-aliases]
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

SOURCE_NAME = "Musicbrainz"
SOURCE = paths.DATA_ROOT / SOURCE_NAME
FK_FILE = paths.DATA_ROOT / "musicBrainzMock" / "Metadata" / "ForeignKeys.csv"
HERE = Path(__file__).resolve().parent


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


CAP = int(flag("cap-mb", "200")) * 1024 * 1024
OUT = Path(flag("out", str(HERE / "specs" / "musicbrainz-full.json")))
NEST_ALIASES = "--nest-aliases" in sys.argv

DOCUMENT_SUFFIXES = ("_alias", "_annotation", "_tag", "_ipi", "_isni", "_gid_redirect")


def store_of(table):
    if table.startswith("l_"):
        return "graph"
    if table.endswith(DOCUMENT_SUFFIXES) and not table.endswith("_alias_type"):
        return "document"
    return "relational"


PREFIX = {"graph": "neo4j:", "document": "mongo.", "relational": "pg."}


def field_count(path):
    with path.open(encoding="utf-8", errors="replace") as handle:
        line = handle.readline()
    return line.rstrip("\n").count("\t") + 1 if line else 0


def main():
    files = {p.name.lower(): p for p in SOURCE.iterdir() if p.is_file()}
    included = {name: path for name, path in files.items() if path.stat().st_size <= CAP}

    with FK_FILE.open(encoding="utf-8", newline="") as handle:
        keys = [{k: v.strip().lower() for k, v in row.items()}
                for row in csv.DictReader(handle, delimiter=";")]

    usable = [k for k in keys
              if k["fk_table"] in included and k["pk_table"] in included]

    # Aliases are nested into the entity they describe, which is the one satellite family whose
    # document reading is unambiguous. The link column is not guessed: it is the foreign key from
    # the alias table to its parent, taken from the same metadata.
    nested = {}
    if NEST_ALIASES:
        for key in usable:
            child, parent = key["fk_table"], key["pk_table"]
            if child.endswith("_alias") and child[: -len("_alias")] == parent:
                nested[child] = {"parent": parent, "child_key": key["fk_column"],
                                 "parent_key": key["pk_column"]}
        # A nested table has no scope of its own, so a foreign key pointing *at* one has no
        # relation to name. Those are dropped from the oracle rather than silently mismatched.
        pointed_at = {k["pk_table"] for k in usable if k["pk_table"] in nested}
        for table in pointed_at:
            nested.pop(table, None)

    columns = {name: [f"column{i + 1}" for i in range(field_count(path))]
               for name, path in included.items()}

    tables = {}
    for name in sorted(included):
        if not columns[name]:
            continue
        store = store_of(name)
        entry = {"store": store, "relation": PREFIX[store] + name, "columns": columns[name]}
        if name in nested:
            entry["skip_own_scope"] = True
        tables[name] = entry
    for child, link in nested.items():
        tables[link["parent"]].setdefault("nest", []).append({
            "table": child, "field": child, "parent_key": link["parent_key"],
            "child_key": link["child_key"],
        })

    def qualify(table, column):
        """Where an attribute ends up once the conversion is applied."""
        if table in nested:
            parent = nested[table]["parent"]
            return f"{tables[parent]['relation']}@{table}.{table}.{column}"
        return f"{tables[table]['relation']}.{column}"

    dependencies, dropped = [], 0
    for key in usable:
        if key["fk_table"] not in tables or key["pk_table"] not in tables:
            dropped += 1
            continue
        if key["fk_column"] not in columns[key["fk_table"]] \
                or key["pk_column"] not in columns[key["pk_table"]]:
            dropped += 1
            continue
        dependent = qualify(key["fk_table"], key["fk_column"])
        referenced = qualify(key["pk_table"], key["pk_column"])
        if dependent == referenced:
            dropped += 1     # a self-reference on one column is trivially true, not a finding
            continue
        dependencies.append({
            "dependent": dependent, "referenced": referenced,
            "models": f"{store_of(key['fk_table'])} -> {store_of(key['pk_table'])}",
            "cross_model": store_of(key["fk_table"]) != store_of(key["pk_table"]),
            "self_table": key["fk_table"] == key["pk_table"],
        })

    spec = {
        "name": "musicbrainz-full",
        "source": SOURCE_NAME,
        "comment": [
            "Generated by experiments/datasets/gen_musicbrainz_spec.py -- do not edit by hand.",
            f"Tables up to {CAP // 2**20} MB are taken whole, so no sampling can turn an exact",
            "foreign key into a partial one. Column names are positional because that is how the",
            "corpus's own foreign-key metadata names them.",
        ],
        "format": {"delimiter": "\t", "nulls": ["\\N"], "header": False, "suffix": ""},
        "anchor": {"table": next(iter(tables)), "key": "column1", "rows": 0,
                   "note": "unused: every table is taken whole"},
        "tables": tables,
        "dependencies": dependencies,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")

    stores = {}
    for entry in tables.values():
        stores[entry["store"]] = stores.get(entry["store"], 0) + 1
    cross = sum(1 for d in dependencies if d["cross_model"])
    print(f"{len(keys)} declared foreign keys in the corpus metadata")
    print(f"{len(usable)} have both endpoints among the {len(included)} tables within "
          f"{CAP // 2**20} MB")
    print(f"{len(dependencies)} usable as an oracle ({dropped} dropped: unmappable or "
          f"single-column self-references)")
    print(f"{cross} of them cross a store boundary\n")
    print(f"  tables per store: " + ", ".join(f"{k} {v}" for k, v in sorted(stores.items())))
    print(f"  nested alias scopes: {len(nested)}")
    print(f"  extractors: {sum(len(c) for n, c in columns.items() if n in tables)}")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
