"""Injects controlled syntactic variation into one store of a meta-level export.

Spelling mode: a fraction of the distinct values in one store's non-identifier columns receives
one single-character edit, so edit distance 1 suffices to match them again. Perturbation is per
distinct value, so a store spells a value the same way consistently.

Transform mode: every value of one column (--relation, --column) is rewritten by a transformation
from experiments/lib/transforms.py (registry prefix, zero-padding, letter case, unit or currency
conversion), so it no longer matches its counterpart in another store under equality.

usage: perturb_export.py <sourceExport> <targetExport> <rate> [--store=graph] [--seed=N]
       perturb_export.py <sourceExport> <targetExport> --transform=<spec> --relation=<r> --column=<c>
                         [--decimals=N]
"""
import csv
import json
import random
import shutil
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import transforms  # noqa: E402

SOURCE = TARGET = RATE = STORE = SEED = None


def flag(name, default=None):
    prefix = f"--{name}="
    return next((a[len(prefix):] for a in sys.argv[1:] if a.startswith(prefix)), default)

# Identifier-like columns are left alone: surrogate keys are copied between systems, not retyped.
IDENTIFIER_HINTS = ("_id", "id", "ids")


def store_of(relation):
    base = relation.split("@")[0]
    if base.startswith("neo4j:"):
        return "graph"
    if base.startswith("unibench.") or base.startswith("shop."):
        return "document"
    return "relational"


def is_identifier(column):
    leaf = column.split(".")[-1].lower()
    return leaf in IDENTIFIER_HINTS or leaf.endswith("_id") or leaf.endswith("_ids")


def edit(value, rng):
    """One single-character edit: substitution, insertion or deletion."""
    if not value:
        return value
    kind = rng.choice(("substitute", "insert", "delete"))
    position = rng.randrange(len(value))
    if kind == "substitute":
        replacement = rng.choice(string.ascii_lowercase)
        if replacement == value[position]:
            replacement = "z" if replacement != "z" else "q"
        return value[:position] + replacement + value[position + 1:]
    if kind == "insert":
        return value[:position] + rng.choice(string.ascii_lowercase) + value[position:]
    return value[:position] + value[position + 1:] if len(value) > 1 else value


def transform_column(source, target, transform, relation, column, decimals=None):
    """Copies an export with every value of relation.column rewritten by `transform`.

    Returns the number of distinct values changed. Other scopes are copied unchanged; the catalog
    is copied as is, since the column keeps its name and the scope its row count.
    """
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    entries = json.loads((source / "catalog.json").read_text(encoding="utf-8"))
    shutil.copyfile(source / "catalog.json", target / "catalog.json")
    changed = set()
    found = False
    for entry in entries:
        src, dst = source / entry["file"], target / entry["file"]
        if entry["relation"] != relation:
            shutil.copyfile(src, dst)
            continue
        with src.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            rows = list(reader)
        if column not in header:
            raise SystemExit(f"{relation} has no column {column}")
        found = True
        i = header.index(column)
        with dst.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            for row in rows:
                if i < len(row) and row[i] != "":
                    new = transforms.apply(transform, row[i], decimals)
                    if new is not None and new != row[i]:
                        changed.add(row[i])
                        row[i] = new
                writer.writerow(row)
    if not found:
        raise SystemExit(f"no scope {relation} in {source}")
    return len(changed)


def main():
    global SOURCE, TARGET, RATE, STORE, SEED
    SOURCE, TARGET = Path(sys.argv[1]), Path(sys.argv[2])
    if flag("transform"):
        # Separate flags: relation and column names may both contain dots
        # (relation shop.orders@lineitems, column lineitems.l_partkey).
        relation, column = flag("relation"), flag("column")
        decimals = int(flag("decimals")) if flag("decimals") else None
        n = transform_column(SOURCE, TARGET, transforms.parse(flag("transform")), relation, column, decimals)
        print(f"{relation}.{column} rewritten by {flag('transform')}: {n} distinct values -> {TARGET}")
        return
    RATE = float(sys.argv[3])
    STORE = flag("store", "graph")
    SEED = int(flag("seed", "20260823"))
    rng = random.Random(SEED)
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)

    entries = json.loads((SOURCE / "catalog.json").read_text(encoding="utf-8"))
    shutil.copyfile(SOURCE / "catalog.json", TARGET / "catalog.json")

    perturbed_columns = touched = distinct = 0
    for entry in entries:
        source_file, target_file = SOURCE / entry["file"], TARGET / entry["file"]
        if store_of(entry["relation"]) != STORE:
            shutil.copyfile(source_file, target_file)
            continue

        with source_file.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            rows = list(reader)

        mapping = {}
        for column in columns:
            if is_identifier(column):
                continue
            values = {row[column] for row in rows if row.get(column)}
            if not values:
                continue
            perturbed_columns += 1
            distinct += len(values)
            for value in sorted(values):
                if rng.random() < RATE:
                    changed = edit(value, rng)
                    if changed != value:
                        mapping[(column, value)] = changed
                        touched += 1

        with target_file.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([mapping.get((c, row.get(c, "")), row.get(c, "")) for c in columns])

    print(f"store '{STORE}' perturbed at rate {RATE}: {touched} of {distinct} distinct values "
          f"across {perturbed_columns} non-identifier columns -> {TARGET}")


if __name__ == "__main__":
    main()
