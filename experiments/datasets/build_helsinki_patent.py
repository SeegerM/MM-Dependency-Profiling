"""Prepares the Helsinki UDBMS Patent dataset for loading into PostgreSQL and Neo4j.

The dataset has five relational tables (patents, inventors, assignees, classifications) and a
citation edge list over the same patent identifiers. Cited patents may predate the table, so the
two citation endpoints reference patent.PATENT to very different degrees (about 0.9996 citing vs
0.8458 cited over the full edge list).

To preserve that, graph nodes are created for every identifier in the sampled citations, not only
for rows of `patent.table`. Citations are uniformly sampled because the exporter holds a
relationship type's endpoints in memory; the relational side is loaded whole.

usage: build_helsinki_patent.py [--source=upstream/helsinki-multimodel] [--out=<work>/hpatent]
                                [--citations=1000000|all] [--seed=20260829]
"""
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "experiments" / "lib"))
import paths  # noqa: E402

csv.field_size_limit(1 << 24)
TABLES = ("patent", "inventor", "assignee", "class", "category")


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


# Tables whose free-text column contains unquoted commas, and the index of that column. Only one
# column per table is free text and it is never first or last, so the fields on either side of it
# stay positionally addressable and the overflow can be re-joined without guesswork.
FREE_TEXT = {"class": 1, "assignee": 1}


def copy_table(source, target, free_text=None):
    """Rewrites one .table file as a plain CSV with lower-cased header names.

    Lower-casing matches PostgreSQL's folded identifiers, so one column does not appear as two
    extractors. Rows with extra fields are rejoined at the table's known free-text column (unquoted
    commas in names); without such a column a ragged row is skipped, since there is no safe way to
    tell which field absorbed the separator.
    """
    rows = repaired = skipped = 0
    with source.open(newline="", encoding="utf-8", errors="replace") as inp, \
            target.open("w", newline="", encoding="utf-8") as out:
        reader = csv.reader(inp)
        writer = csv.writer(out, lineterminator="\n")
        header = [name.strip().lower() for name in next(reader)]
        writer.writerow(header)
        width = len(header)
        for row in reader:
            if len(row) > width and free_text is not None:
                overflow = len(row) - width
                row = (row[:free_text]
                       + [",".join(row[free_text:free_text + overflow + 1])]
                       + row[free_text + overflow + 1:])
                repaired += 1
            if len(row) != width:
                skipped += 1
                continue
            writer.writerow(row)
            rows += 1
    return rows, repaired, skipped


def sample_citations(source, target, limit, seed):
    """Uniform reservoir sample of the citation edge list, or all of it when limit is None."""
    with source.open(newline="", encoding="utf-8", errors="replace") as inp:
        reader = csv.reader(inp)
        header = [name.strip().lower() for name in next(reader)]
        citing, cited = header.index("citing"), header.index("cited")
        rng = random.Random(seed)
        reservoir = []
        seen = 0
        for row in reader:
            if len(row) <= max(citing, cited):
                continue
            pair = (row[citing], row[cited])
            seen += 1
            if limit is None or len(reservoir) < limit:
                reservoir.append(pair)
            else:
                j = rng.randrange(seen)
                if j < limit:
                    reservoir[j] = pair
    nodes = set()
    with target.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["citing", "cited"])
        for pair in reservoir:
            writer.writerow(pair)
            nodes.add(pair[0])
            nodes.add(pair[1])
    return seen, len(reservoir), nodes


def main():
    source = Path(flag("source", str(HERE / "upstream" / "helsinki-multimodel")))
    out = Path(flag("out", str(paths.WORK_ROOT / "hpatent")))
    citations_flag = flag("citations", "1000000")
    limit = None if citations_flag == "all" else int(citations_flag)
    seed = int(flag("seed", "20260829"))
    out.mkdir(parents=True, exist_ok=True)

    processed = source / "data" / "Patent_dataset" / "processed_dataset"
    for name in TABLES:
        count, repaired, skipped = copy_table(
            processed / (name + ".table"), out / ("pg_" + name + ".csv"), FREE_TEXT.get(name))
        note = ""
        if repaired:
            note += "  (%d rows rejoined on an unquoted comma)" % repaired
        if skipped:
            note += "  (%d ragged rows skipped)" % skipped
        print("  %-14s %10d rows%s" % (name, count, note))

    seen, kept, nodes = sample_citations(
        processed / "citation.graph", out / "neo4j_citations.csv", limit, seed)
    print("  %-14s %10d rows sampled from %d" % ("citations", kept, seen))

    # Every identifier the sampled edges touch, including those missing from the relational table.
    with (out / "neo4j_patent_nodes.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["id"])
        for identifier in sorted(nodes):
            writer.writerow([identifier])
    print("  %-14s %10d distinct endpoints" % ("patent nodes", len(nodes)))
    print("written to " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
