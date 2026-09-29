"""Converts a single-model corpus into a multi-model instance, driven by a declarative spec.

The TPC-H generator in build_dataset.py and the clean/dirty generator in build_dirty_dataset.py are
two instances of one pattern: choose relations, assign each to a data model, turn delimited fields
and child tables into nested scopes, and keep the source's foreign keys as ground truth. This is
that pattern factored out, so a corpus with a documented schema can be converted by writing a spec
rather than a program.

What a spec has to say, and why:

  anchor        which table bounds the instance, and how many of its rows to take. Every other
                table is restricted to rows referencing those, so the result is referentially
                closed and every declared dependency still holds on the subset.
  key_sets      further key sets derived from earlier ones, for schemas where closure runs through
                more than one hop. Restricting a real corpus to one key set is rarely enough:
                keeping the credits of a chosen set of releases means also keeping the artists
                those credits name, and the areas those artists sit in.
  tables        for each source table, the store it goes to and the columns to keep.
  arrays        a delimited field becomes its own scope, one observation per element. This is
                where real corpora already contain what the paper argues about: IMDB's
                knownForTitles is a comma-separated list of title identifiers, i.e. a
                collection-valued foreign key.
  nest          a child table embedded into a parent as an array scope, the way a document store
                would hold it rather than as a separate relation.
  dependencies  the source schema's foreign keys, carried over as the oracle.

The output is a meta-level export -- one CSV per scope plus a catalog -- which is what every
runner here consumes. Writing it directly rather than loading the three stores and exporting them
back keeps a conversion independent of docker; the store round trip is exercised elsewhere.

usage: build_multimodel.py <spec.json> [outDir] [--rows=N]
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

SPEC = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
POSITIONAL = [a for a in sys.argv[2:] if not a.startswith("--")]
ROWS = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--rows=")), None)

SOURCE = paths.DATA_ROOT / SPEC["source"]
OUT = Path(POSITIONAL[0]) if POSITIONAL else paths.WORK_ROOT / SPEC["name"] / "metalevel"
FORMAT = SPEC.get("format", {})
DELIMITER = FORMAT.get("delimiter", ",")
NULLS = set(FORMAT.get("nulls", ["\\N"]))
HAS_HEADER = FORMAT.get("header", True)
SUFFIX = FORMAT.get("suffix", "")

csv.field_size_limit(1 << 30)


def clean(value):
    return "" if value in NULLS else value


def read_table(name, limit=None, keep=None):
    """Streams one source table, yielding dicts. Columns come from the header when there is one,
    otherwise from the spec, otherwise positionally as column1..columnN -- the convention the
    corpus's own foreign-key metadata uses when the dump carries no names."""
    declared = SPEC["tables"][name].get("source_columns")
    path = SOURCE / f"{name}{SUFFIX}"
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle, delimiter=DELIMITER, quoting=csv.QUOTE_NONE)
        header = next(reader) if HAS_HEADER else None
        columns = declared or header
        emitted = 0
        for fields in reader:
            if columns is None:
                columns = [f"column{i + 1}" for i in range(len(fields))]
            row = {c: clean(v) for c, v in zip(columns, fields)}
            if keep and not keep(row):
                continue
            yield row
            emitted += 1
            if limit is not None and emitted >= limit:
                return


def write(relation, store, scope, columns, rows, catalog):
    file_name = relation.replace(":", "_").replace("@", "_at_") + ".csv"
    with (OUT / file_name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(c, "") for c in columns])
    catalog.append({"relation": relation, "store": store, "scope": scope, "rows": len(rows),
                    "columns": len(columns), "file": file_name})
    print(f"  {relation:<34} {store:<11} {len(rows):>9,} rows x {len(columns)}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    catalog = []

    anchor = SPEC["anchor"]
    # A spec that takes every table whole declares no anchor row count. Zero must mean "no limit"
    # rather than "no rows", or the anchor table itself would be reduced to a single row.
    anchor_rows = ROWS or anchor.get("rows") or None
    key_sets = {"anchor": {row[anchor["key"]]
                           for row in read_table(anchor["table"], limit=anchor_rows)}}
    print(f"{SPEC['name']}: anchored on {anchor['table']}.{anchor['key']}, "
          f"{len(key_sets['anchor']):,} keys")

    def selector(restrict):
        """Row test for a restrict_by clause, against whichever key set it names."""
        if not restrict:
            return lambda row: True
        target = key_sets[restrict.get("set", "anchor")]
        column, split = restrict["column"], restrict.get("split")
        mode = restrict.get("mode", "all")

        def test(row):
            value = row.get(column, "")
            if not split:
                return value in target
            # A restricting column may itself be a delimited list. "any" keeps a row when one
            # element lands inside the key set, which does *not* close the instance: the row's
            # other elements still reference rows outside it, and the foreign key then reads as
            # partial for a reason that is an artifact of sampling rather than a property of the
            # data. "all" is what keeps the promise the anchor makes.
            parts = [p for p in value.split(split) if p]
            return bool(parts) and (all if mode == "all" else any)(p in target for p in parts)
        return test

    for derived in SPEC.get("key_sets", []):
        keep = selector(derived.get("restrict_by"))
        key_sets[derived["name"]] = {
            row[derived["column"]]
            for row in read_table(derived["table"], limit=derived.get("rows"))
            if keep(row) and row.get(derived["column"])
        }
        print(f"  key set '{derived['name']}' from {derived['table']}.{derived['column']}: "
              f"{len(key_sets[derived['name']]):,}")
    print()

    for name, table in SPEC["tables"].items():
        relation, store = table["relation"], table["store"]
        columns = table["columns"]
        restrict = table.get("restrict_by")

        keep = selector(restrict)

        # The anchor table bounds itself: it is read to the same row count that produced the key
        # set, so the instance is exactly the closure of those rows and not the whole corpus.
        limit = anchor_rows if name == anchor["table"] else table.get("limit")
        rows = list(read_table(name, keep=keep, limit=limit))

        # A table that exists only to be nested into a parent contributes no scope of its own.
        # Emitting one anyway would duplicate every value and manufacture inclusions between the
        # two copies -- the same artifact the parent reference produces in MetaLevelExporter.
        if table.get("skip_own_scope"):
            continue

        base_columns = [c for c in columns if c not in table.get("arrays", {})]
        write(relation, store, table.get("scope", "base"), base_columns, rows, catalog)

        for field, options in table.get("arrays", {}).items():
            elements = []
            for row in rows:
                value = row.get(field, "")
                if not value:
                    continue
                for part in value.split(options["split"]):
                    if part.strip():
                        elements.append({field: part.strip()})
            write(f"{relation}@{field}", store, field, [field], elements, catalog)

        for child in table.get("nest", []):
            child_table = SPEC["tables"][child["table"]]
            parent_keys = {row[child["parent_key"]] for row in rows}
            child_restrict = child_table.get("restrict_by")

            own_restriction = selector(child_restrict)

            def child_keep(row, parent_keys=parent_keys, child_key=child["child_key"],
                           own_restriction=own_restriction):
                # The child's own restriction still applies: a credit is kept only when both its
                # person is in the document store and its title is inside the anchor set.
                return row.get(child_key) in parent_keys and own_restriction(row)

            child_rows = [
                {f"{child['field']}.{c}": row[c] for c in child_table["columns"]}
                for row in read_table(child["table"], keep=child_keep)
            ]
            write(f"{relation}@{child['field']}", store, child["field"],
                  [f"{child['field']}.{c}" for c in child_table["columns"]], child_rows, catalog)

    body = ",\n".join("  " + json.dumps(entry) for entry in catalog)
    (OUT / "catalog.json").write_text("[\n" + body + "\n]\n", encoding="utf-8")

    (OUT.parent / "ground_truth.json").write_text(json.dumps({
        "source": SPEC["source"], "name": SPEC["name"],
        "dependencies": SPEC["dependencies"],
    }, indent=2), encoding="utf-8")

    stores = {entry["store"] for entry in catalog}
    print(f"\n{len(catalog)} observation sets across {len(stores)} stores, "
          f"{sum(e['columns'] for e in catalog)} extractors -> {OUT}")
    cross = sum(1 for d in SPEC["dependencies"] if d.get("cross_model", True))
    print(f"{len(SPEC['dependencies'])} declared dependencies, {cross} of them cross-model")


if __name__ == "__main__":
    main()
