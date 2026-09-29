"""Applies one controlled, isolated change to a meta-level export.

Each mode changes exactly one kind of heterogeneity and leaves the others alone, so the
Heterogeneity Profile can be calibrated component by component.

  rename        renames a fraction of one store's extractors. The values are untouched, so every
                dependency still holds; only the vocabulary differs.
  denormalize   merges two observation sets of one store into their join. The values are again
                untouched, but the merged scope repeats the referenced side's attributes once per
                referencing row, which is what a lower degree of normalization means.

Value-level variation is handled by experiments/datasets/perturb_export.py.

usage: vary_export.py <sourceExport> <targetExport> --mode=rename --store=relational --rate=1.0
       vary_export.py <sourceExport> <targetExport> --mode=denormalize
                      --join=public.invoice,public.customer,customer_id
"""
import csv
import json
import random
import shutil
import sys
from pathlib import Path


def flag(name, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
SOURCE, TARGET = Path(POSITIONAL[0]), Path(POSITIONAL[1])
MODE = flag("mode", "rename")
STORE = flag("store", "relational")
RATE = float(flag("rate", "1.0"))
SEED = int(flag("seed", "20260823"))


def store_of(relation):
    base = relation.split("@")[0]
    if base.startswith("neo4j:"):
        return "graph"
    if base.startswith("unibench.") or base.startswith("shop."):
        return "document"
    return "relational"


def read(path):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return (reader.fieldnames or []), list(reader)


def write(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(c, "") for c in columns])


def rename_mode(entries):
    """A store-local vocabulary: renamed extractors carry a prefix, values are untouched.

    A prefix rather than synonyms guarantees no renamed extractor collides with an existing name.
    """
    rng = random.Random(SEED)
    renamed = total = 0
    for entry in entries:
        columns, rows = read(SOURCE / entry["file"])
        if store_of(entry["relation"]) != STORE:
            write(TARGET / entry["file"], columns, rows)
            continue
        mapping = {}
        for column in columns:
            total += 1
            if rng.random() < RATE:
                mapping[column] = f"alt_{column}"
                renamed += 1
        new_columns = [mapping.get(c, c) for c in columns]
        renamed_rows = [{mapping.get(c, c): row.get(c, "") for c in columns} for row in rows]
        write(TARGET / entry["file"], new_columns, renamed_rows)
    print(f"renamed {renamed} of {total} extractors in store '{STORE}'")
    return entries


def denormalize_mode(entries):
    """Replaces two observation sets by their join, leaving every other set untouched."""
    left_name, right_name, key = flag("join").split(",")
    by_name = {entry["relation"]: entry for entry in entries}
    left, right = by_name[left_name], by_name[right_name]

    left_columns, left_rows = read(SOURCE / left["file"])
    right_columns, right_rows = read(SOURCE / right["file"])
    index = {row[key]: row for row in right_rows if row.get(key)}

    columns = left_columns + [c for c in right_columns if c != key]
    merged = []
    for row in left_rows:
        partner = index.get(row.get(key, ""))
        if partner is None:
            continue
        combined = dict(row)
        combined.update({c: partner.get(c, "") for c in right_columns if c != key})
        merged.append(combined)

    relation = f"{left_name}+{right_name.split('.')[-1]}"
    file_name = relation.replace(":", "_").replace("+", "_plus_") + ".csv"
    write(TARGET / file_name, columns, merged)

    kept = [e for e in entries if e["relation"] not in (left_name, right_name)]
    for entry in kept:
        shutil.copyfile(SOURCE / entry["file"], TARGET / entry["file"])
    kept.append({"relation": relation, "scope": "denormalized", "rows": len(merged),
                 "columns": len(columns), "file": file_name})
    print(f"merged {left_name} and {right_name} on {key}: {len(merged):,} rows x {len(columns)} "
          f"columns, {len(entries)} observation sets -> {len(kept)}")
    return kept


def main():
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    entries = json.loads((SOURCE / "catalog.json").read_text(encoding="utf-8"))

    entries = rename_mode(entries) if MODE == "rename" else denormalize_mode(entries)

    body = ",\n".join("  " + json.dumps(entry) for entry in entries)
    (TARGET / "catalog.json").write_text("[\n" + body + "\n]\n", encoding="utf-8")
    print(f"-> {TARGET}")


if __name__ == "__main__":
    main()
