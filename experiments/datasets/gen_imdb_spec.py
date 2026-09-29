"""Generates a multi-model conversion spec for IMDB's full schema.

The MusicBrainz counterpart derives its spec from a foreign-key list the corpus ships. IMDB ships
no such file, so the keys below are transcribed from IMDb's published dataset documentation. That
is a weaker provenance and is stated as such: the oracle is a documented schema rather than a
machine-readable one, and it is small enough to be checked by reading it.

What IMDB offers that MusicBrainz does not is collection-valued foreign keys occurring naturally.
Three of the ten declared keys are comma-separated lists of identifiers -- a title's directors and
writers, and the titles a person is known for. Each is a foreign key that a single-relation adapter
observes as one literal and therefore cannot find at any threshold.

Whole tables only, up to a size cap, for the same reason as MusicBrainz: IMDB's title and name
tables reference each other in both directions, so a sampled instance has no referential closure
short of a fixpoint that expands to the entire corpus.

usage: gen_imdb_spec.py [--cap-mb=900] [--out=specs/imdb-full.json]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

SOURCE_NAME = "IMDB"
SOURCE = paths.DATA_ROOT / SOURCE_NAME
HERE = Path(__file__).resolve().parent


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


CAP = int(flag("cap-mb", "900")) * 1024 * 1024
OUT = Path(flag("out", str(HERE / "specs" / "imdb-full.json")))

# Store assignment follows what each table is. Titles are entities; crew and episode are
# relationships between titles and people or between titles and their parents, i.e. edges; a person
# carries two list-valued fields and is the one entity a document store holds naturally; ratings
# and alternative titles are flat attribute tables.
TABLES = {
    "basics": {"store": "graph", "relation": "neo4j:Title",
               "arrays": {"genres": ","}},
    "cew": {"store": "graph", "relation": "neo4j:Crew",
            "arrays": {"directors": ",", "writers": ","}},
    "episode": {"store": "graph", "relation": "neo4j:Episode"},
    "name": {"store": "document", "relation": "mongo.person",
             "arrays": {"primaryProfession": ",", "knownForTitles": ","}},
    "rating": {"store": "relational", "relation": "pg.title_rating"},
    "akas": {"store": "relational", "relation": "pg.title_akas"},
    "principals": {"store": "relational", "relation": "pg.title_principals"},
}

# IMDb's documented foreign keys. (table, column, is_list) -> (table, column).
KEYS = [
    ("cew", "tconst", False, "basics", "tconst"),
    ("cew", "directors", True, "name", "nconst"),
    ("cew", "writers", True, "name", "nconst"),
    ("episode", "tconst", False, "basics", "tconst"),
    ("episode", "parentTconst", False, "basics", "tconst"),
    ("rating", "tconst", False, "basics", "tconst"),
    ("akas", "titleId", False, "basics", "tconst"),
    ("principals", "tconst", False, "basics", "tconst"),
    ("principals", "nconst", False, "name", "nconst"),
    ("name", "knownForTitles", True, "basics", "tconst"),
]


def header(path):
    with path.open(encoding="utf-8", errors="replace") as handle:
        return handle.readline().rstrip("\n").split("\t")


def main():
    included, skipped = {}, {}
    for name, config in TABLES.items():
        path = SOURCE / f"{name}.csv"
        if not path.exists():
            skipped[name] = "no data file"
            continue
        size = path.stat().st_size
        if size > CAP:
            skipped[name] = f"{size / 2**20:,.0f} MB exceeds the cap"
            continue
        included[name] = {**config, "columns": header(path), "size": size}

    tables = {}
    for name, config in included.items():
        entry = {"store": config["store"], "relation": config["relation"],
                 "columns": config["columns"]}
        if config.get("arrays"):
            entry["arrays"] = {field: {"split": sep}
                               for field, sep in config["arrays"].items()
                               if field in config["columns"]}
        tables[name] = entry

    def qualify(table, column, is_list):
        relation = tables[table]["relation"]
        return f"{relation}@{column}.{column}" if is_list else f"{relation}.{column}"

    dependencies, unusable = [], []
    for child, column, is_list, parent, key in KEYS:
        if child not in tables or parent not in tables:
            unusable.append((f"{child}.{column} -> {parent}.{key}",
                             skipped.get(child) or skipped.get(parent)))
            continue
        dependencies.append({
            "dependent": qualify(child, column, is_list),
            "referenced": qualify(parent, key, False),
            "models": f"{tables[child]['store']} -> {tables[parent]['store']}",
            "cross_model": tables[child]["store"] != tables[parent]["store"],
            "collection_valued": is_list,
        })

    spec = {
        "name": "imdb-full",
        "source": SOURCE_NAME,
        "comment": [
            "Generated by experiments/datasets/gen_imdb_spec.py -- do not edit by hand.",
            f"Tables up to {CAP // 2**20} MB are taken whole, so no sampling can turn an exact",
            "foreign key into a partial one. IMDB ships no machine-readable foreign-key list; the",
            "keys come from IMDb's published dataset documentation and are transcribed in the",
            "generator, where they can be read and checked.",
        ],
        "format": {"delimiter": "\t", "nulls": ["\\N"], "header": True, "suffix": ".csv"},
        "anchor": {"table": next(iter(tables)), "key": "tconst", "rows": 0,
                   "note": "unused: every table is taken whole"},
        "tables": tables,
        "dependencies": dependencies,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")

    total = sum(c["size"] for c in included.values())
    cross = sum(1 for d in dependencies if d["cross_model"])
    lists = sum(1 for d in dependencies if d["collection_valued"])
    print(f"{len(TABLES)} tables in the schema, {len(included)} within {CAP // 2**20} MB "
          f"({total / 2**30:.2f} GB)")
    for name, why in skipped.items():
        print(f"  excluded {name:<12} {why}")
    print(f"\n{len(KEYS)} documented foreign keys, {len(dependencies)} usable")
    for label, why in unusable:
        print(f"  unusable {label:<44} {why}")
    print(f"  {cross} cross a store boundary")
    print(f"  {lists} are collection-valued")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
