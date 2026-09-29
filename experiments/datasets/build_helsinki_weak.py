"""Prepares the Helsinki UDBMS Film and University datasets, the two weak-correspondence cases.

Film: an IMDb metadata dump keyed by film title, against DBpedia and YAGO graphs whose subjects
are underscored, bracketed titles (`<Electric_Shadows>`). Titles are neither unique nor
consistently named, so the cross-store match is an entity-resolution problem rather than an IND.

University: a College Scorecard table (`UNITID` declared unique) against a location graph of place
names and coordinates, with no shared identifier.

As for Person, node sets are written complete and edges are sampled.

usage: build_helsinki_weak.py [--source=upstream/helsinki-multimodel] [--work=$WORK_ROOT]
                              [--which=film,university] [--triples=800000|all] [--seed=20260831]
"""
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402

HERE = Path(__file__).resolve().parents[2]
csv.field_size_limit(1 << 24)


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def strip_angles(value):
    """Graph terms are written `<Name>` in these files; the brackets are syntax, not identity."""
    if value.startswith("<") and value.endswith(">"):
        return value[1:-1]
    return value


def is_literal(value):
    return value.startswith('"')


def read_triples(source, nodes, edges_out, limit, seed, transform=strip_angles):
    """Collects every resource node and writes a bounded uniform sample of the edges."""
    total = 0
    with source.open(encoding="utf-8", errors="replace") as inp:
        for line in inp:
            total += 1
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 3:
                continue
            nodes.add(transform(parts[0]))
            if not is_literal(parts[2]):
                nodes.add(transform(parts[2]))

    keep = total if limit is None else min(limit, total)
    chosen = None
    if keep < total:
        chosen = set(random.Random(seed).sample(range(total), keep))
    written = 0
    with source.open(encoding="utf-8", errors="replace") as inp:
        for index, line in enumerate(inp):
            if chosen is not None and index not in chosen:
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 3 or is_literal(parts[2]):
                continue
            edges_out.writerow([transform(parts[0]),
                                strip_angles(parts[1]).lstrip("-"),
                                transform(parts[2])])
            written += 1
    return written, total


def build_film(processed, work, limit, seed):
    work.mkdir(parents=True, exist_ok=True)
    films = 0
    with (processed / "film.imdb.json").open(encoding="utf-8", errors="replace") as inp, \
            (work / "films.jsonl").open("w", encoding="utf-8") as out:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Only identifying fields are kept; the free-text plot and poster URL are dropped.
            out.write(json.dumps({
                "title": record.get("Title"),
                "year": record.get("Year"),
                "rated": record.get("Rated"),
                "genre": record.get("Genre"),
                "director": record.get("Director"),
                "imdb_id": record.get("imdbID"),
                "imdb_rating": record.get("imdbRating"),
            }, ensure_ascii=False) + "\n")
            films += 1
    print("  film documents        {:,}".format(films))

    for name, source in (("dbpedia", "film.dbpedia.graph"), ("yago", "film.yago.graph")):
        nodes = set()
        with (work / ("film_%s_edges.csv" % name)).open("w", encoding="utf-8",
                                                        newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["subject", "predicate", "object"])
            kept, total = read_triples(processed / source, nodes, writer, limit, seed)
        with (work / ("film_%s_nodes.csv" % name)).open("w", encoding="utf-8",
                                                        newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["id"])
            for node in sorted(nodes):
                writer.writerow([node])
        print("  film %-8s nodes {:,}   edges {:,} of {:,}".replace("%-8s", name.ljust(8))
              .format(len(nodes), kept, total))


def build_university(processed, work, limit, seed):
    work.mkdir(parents=True, exist_ok=True)
    rows = 0
    with (processed / "university_scorecard.table").open(encoding="utf-8-sig",
                                                         errors="replace", newline="") as inp, \
            (work / "scorecard.csv").open("w", encoding="utf-8", newline="") as out:
        reader = csv.reader(inp)
        writer = csv.writer(out, lineterminator="\n")
        header = next(reader)
        writer.writerow(header)
        for row in reader:
            writer.writerow(row)
            rows += 1
    print("  scorecard rows        {:,} x {:,} columns".format(rows, len(header)))

    # PostgreSQL caps a table at 1,600 columns and the scorecard has 1,729. The subset keeps the
    # identifying and locating columns; the rest are outcome statistics. The full file is kept too.
    keep = ["UNITID", "OPEID", "opeid6", "INSTNM", "CITY", "STABBR",
            "ZIP", "INSTURL", "LATITUDE", "LONGITUDE"]
    missing = [c for c in keep if c not in header]
    if missing:
        raise SystemExit("scorecard is missing expected columns: " + ", ".join(missing))
    index = [header.index(c) for c in keep]
    with (work / "scorecard.csv").open(newline="", encoding="utf-8") as inp, \
            (work / "scorecard_subset.csv").open("w", encoding="utf-8", newline="") as out:
        reader = csv.reader(inp)
        writer = csv.writer(out, lineterminator="\n")
        next(reader)
        writer.writerow([c.lower() for c in keep])
        for row in reader:
            writer.writerow([row[i] if i < len(row) else "" for i in index])
    print("  scorecard subset      {} columns (PostgreSQL caps a relation at 1,600)"
          .format(len(keep)))

    # Every object in the location graph is a literal, so its triples are pivoted into node
    # properties rather than loaded as edges.
    properties = {}
    predicates = set()
    total = 0
    with (processed / "location.graph").open(encoding="utf-8", errors="replace") as inp:
        for line in inp:
            total += 1
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 3:
                continue
            subject, predicate, value = parts
            if is_literal(value):
                # `"11.5"^^<...#float>` -- the lexical form is the value, the datatype is syntax.
                value = value.split('"^^')[0].lstrip('"').rstrip('"')
            predicates.add(predicate)
            properties.setdefault(subject, {})[predicate] = value

    columns = ["id"] + sorted(predicates)
    with (work / "location_nodes.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        for subject in sorted(properties):
            attributes = properties[subject]
            writer.writerow([subject] + [attributes.get(p, "") for p in columns[1:]])
    print("  location nodes        {:,} with {} properties, from {:,} triples"
          .format(len(properties), len(predicates), total))


def main():
    source = Path(flag("source", str(HERE / "upstream" / "helsinki-multimodel")))
    work = Path(flag("work", str(paths.WORK_ROOT)))
    which = set(flag("which", "film,university").split(","))
    triples = flag("triples", "800000")
    seed = int(flag("seed", "20260831"))
    limit = None if triples == "all" else int(triples)

    if "film" in which:
        print("Film")
        build_film(source / "data" / "Film_dataset" / "processed_dataset",
                   work / "hfilm", limit, seed)
    if "university" in which:
        print("University")
        build_university(source / "data" / "University_dataset" / "processed_dataset",
                         work / "huniversity", limit, seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
