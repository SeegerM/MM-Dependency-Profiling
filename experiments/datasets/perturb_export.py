"""Injects controlled syntactic variation into one store of a meta-level export.

The clean UniBench instance records the same entity identically in every store, so equality
matching suffices and similarity matching has nothing to add. That is a property of the generator,
not of multi-model data: when two systems record the same real-world entity independently, their
spellings diverge. This script reproduces that divergence at a known rate so the two matching
relations can be compared on the same dependencies.

The perturbation is applied per *distinct value*, not per occurrence. A store that spells a vendor
differently spells it that way consistently; perturbing occurrences independently would model a
store with random noise inside a single column, which is a different and less interesting failure.

Each selected value receives exactly one single-character edit, so edit distance 1 suffices to
match it again. That makes the experiment a test of whether the pipeline uses the similarity
measure correctly, not a test of how large tau must be.

usage: perturb_export.py <sourceExport> <targetExport> <rate> [--store=graph] [--seed=N]
"""
import csv
import json
import random
import shutil
import string
import sys
from pathlib import Path

SOURCE = Path(sys.argv[1])
TARGET = Path(sys.argv[2])
RATE = float(sys.argv[3])
STORE = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--store=")), "graph")
SEED = int(next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--seed=")), "20260823"))

# Identifier-like columns are left alone. Divergent spelling is a property of names and free text
# recorded by people; a surrogate key is copied between systems, not retyped. Perturbing those
# would inflate the effect with a kind of variation the motivation does not claim exists.
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


def main():
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
