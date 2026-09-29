"""Converts a single-model corpus into a multi-model instance, driven by a declarative spec.

Chooses relations, assigns each to a data model, turns delimited fields and child tables into
nested scopes, and keeps the source's foreign keys as ground truth.

Spec fields:

  anchor        which table bounds the instance, and how many of its rows to take. Every other
                table is restricted to rows referencing those, so the result is referentially
                closed and every declared dependency still holds on the subset.
  key_sets      further key sets derived from earlier ones, for schemas where closure runs through
                more than one hop (e.g. releases -> credited artists -> their areas).
  tables        for each source table, the store it goes to and the columns to keep.
  arrays        a delimited field becomes its own scope, one observation per element (e.g. IMDB's
                comma-separated knownForTitles, a collection-valued foreign key).
  nest          a child table embedded into a parent as an array scope.
  dependencies  the source schema's foreign keys, carried over as the oracle.

The output is a meta-level export (one CSV per scope plus a catalog), written directly without
loading the stores.

usage: build_multimodel.py <spec.json> [outDir] [--rows=N]
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths
import masked_scope

SPEC = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
POSITIONAL = [a for a in sys.argv[2:] if not a.startswith("--")]
ROWS = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--rows=")), None)
DEFINEDNESS = '--definedness' in sys.argv

SOURCE = paths.DATA_ROOT / SPEC["source"]
OUT = Path(POSITIONAL[0]) if POSITIONAL else paths.WORK_ROOT / SPEC["name"] / "metalevel"
FORMAT = SPEC.get("format", {})
DELIMITER = FORMAT.get("delimiter", ",")
NULLS = set(FORMAT.get("nulls", ["\\N"]))
HAS_HEADER = FORMAT.get("header", True)
SUFFIX = FORMAT.get("suffix", "")

csv.field_size_limit(1 << 30)


def clean(value):
    return (None if DEFINEDNESS else "") if value in NULLS else value


def read_table(name, limit=None, keep=None):
    """Streams one source table, yielding dicts. Columns come from the header when there is one,
    otherwise from the spec, otherwise positionally as column1..columnN -- the convention the
    corpus's own foreign-key metadata uses when the dump carries no names."""
    declared = SPEC["tables"][name].get("source_columns")
    path = SOURCE / f"{name}{SUFFIX}"
    with path.open(encoding="utf-8", errors="strict" if DEFINEDNESS else "replace", newline="") as handle:
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
    file_name = relation.replace(":", "_").replace("@", "_at_") + (".mscope" if DEFINEDNESS else ".csv")
    if DEFINEDNESS:
        masked_scope.write(OUT/file_name,columns,([row.get(c) for c in columns] for row in rows))
        catalog.append({"relation":relation,"store":store,"scope":scope,"rows":len(rows),
                        "columns":len(columns),"file":file_name})
        print(f"  {relation:<34} {store:<11} {len(rows):>9,} rows x {len(columns)}",flush=True)
        return
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
            # A restricting column may itself be a delimited list. "all" keeps the instance
            # referentially closed; "any" does not, so the foreign key can read as partial
            # purely because of sampling.
            parts = [p for p in (value or "").split(split) if p]
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

        # A table that exists only to be nested into a parent contributes no scope of its own;
        # emitting one would duplicate every value and create inclusions between the copies.
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


def stream_full_definedness():
    """Unrestricted full snapshots need no in-memory anchor or table materialization."""
    from contextlib import ExitStack
    OUT.mkdir(parents=True,exist_ok=True); catalog=[]
    for name,table in SPEC['tables'].items():
        if table.get('skip_own_scope'): continue
        relation=table['relation']; arrays=table.get('arrays',{})
        base=[c for c in table['columns'] if c not in arrays]
        definitions=[(relation,table.get('scope','base'),base)]
        definitions += [(relation+'@'+c,c,[c]) for c in arrays]
        with ExitStack() as stack:
            outputs=[]
            for rel,scope,cols in definitions:
                file=rel.replace(':','_').replace('@','_at_')+'.mscope'
                stream=stack.enter_context((OUT/file).open('w',encoding='utf-8',newline=''))
                writer=csv.writer(stream,quoting=csv.QUOTE_ALL,lineterminator='\n')
                writer.writerow(['#multimodel-scope-v2','defined',*cols])
                outputs.append([writer,0,dict(relation=rel,scope=scope,store=table['store'],columns=len(cols),file=file)])
            def emit(index,values):
                output=outputs[index]
                output[0].writerow([output[1],''.join('0' if v is None else '1' for v in values),
                                   *('' if v is None else v for v in values)])
                output[1]+=1
            for row in read_table(name,limit=table.get('limit')):
                emit(0,[row.get(c) for c in base])
                for index,(column,options) in enumerate(arrays.items(),1):
                    value=row.get(column)
                    if value:
                        for part in value.split(options['split']):
                            if part.strip(): emit(index,[part.strip()])
            for _,count,entry in outputs:
                entry['rows']=count; catalog.append(entry)
                print(entry['relation'],count,flush=True)
    (OUT/'catalog.json').write_text(json.dumps(catalog,indent=2),encoding='utf-8')

if __name__ == "__main__":
    unrestricted=not (ROWS or SPEC['anchor'].get('rows') or SPEC.get('key_sets')) and all(
        not t.get('restrict_by') and not t.get('nest') for t in SPEC['tables'].values())
    if DEFINEDNESS and unrestricted: stream_full_definedness()
    else: main()
    if DEFINEDNESS:
        (OUT/'source-policy.json').write_text(json.dumps(dict(source=str(SOURCE),spec=str(Path(sys.argv[1]).resolve()),
            missing_markers=sorted(NULLS),delimiter=DELIMITER,header=HAS_HEADER,
            scalar_policy='Preserve source strings except declared missing markers; strict UTF-8 decoding',
            array_policy='Existing declared split and nonempty stripped elements',
            source_kind='raw-source-conversion'),indent=2),encoding='utf-8')
        if unrestricted:
            (OUT.parent/'ground_truth.json').write_text(json.dumps(dict(source=SPEC['source'],name=SPEC['name'],
                dependencies=SPEC['dependencies']),indent=2),encoding='utf-8')
