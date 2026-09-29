"""Prepares the Helsinki UDBMS Person dataset for loading into MongoDB and Neo4j.

The documents are Wikipedia API responses; the graph is an RDF-style triple list. The two sides
join on a natural identifier spelled differently:

    document  query.pages.<pageid>.title      "Henry Standing Bear"
    graph     subject                         "Henry_Standing_Bear"

so an equality inclusion on `title` finds nothing, while treating spaces and underscores as equal
recovers it. `query.normalized.from` carries the underscored form where the API normalised the
input, giving a second extractor that matches the graph under plain equality.

The graph is emitted as an edge list over predicates. Triples are sampled because the exporter
holds a relationship type's endpoints in memory.

usage: build_helsinki_person.py [--source=upstream/helsinki-multimodel] [--out=<work>/hperson]
                                [--triples=800000|all] [--seed=20260831]
"""
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402

HERE = Path(__file__).resolve().parents[2]


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def build_documents(source, target):
    """Flattens each API response into one document per page.

    Each record wraps exactly one page in the Wikipedia API query envelope, so dropping the
    envelope is lossless.
    """
    written = 0
    with source.open(encoding="utf-8", errors="replace") as inp, \
            target.open("w", encoding="utf-8", newline="") as out:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            query = record.get("query") or {}
            pages = query.get("pages") or {}
            # `normalized` maps the requested spelling to the displayed one. It is absent when the
            # API normalised nothing, and then the underscored form is unavailable for that page.
            normalized = {entry.get("to"): entry.get("from")
                          for entry in (query.get("normalized") or [])}
            for page in pages.values():
                title = page.get("title")
                document = {
                    "pageid": page.get("pageid"),
                    "ns": page.get("ns"),
                    "title": title,
                    "normalized_from": normalized.get(title),
                    "thumbnail": (page.get("thumbnail") or {}).get("original"),
                }
                out.write(json.dumps(document, ensure_ascii=False) + "\n")
                written += 1
    return written


def is_literal(value):
    """Whether a triple object is an RDF literal rather than a resource.

    The file writes literals in Turtle's shape: a quoted lexical form, optionally followed by a
    language tag or a datatype. Anything else names a resource.
    """
    return value.startswith('"')


def build_graph(source, nodes_target, edges_target, limit, seed):
    """Writes every node the triples name, and a uniform sample of the triples as edges.

    The node file is complete, so the title-to-node correspondence is unaffected by sampling;
    only the edges are bounded, since the exporter holds a relationship type's endpoints in memory.
    """
    total = 0
    literals = 0
    nodes = set()
    with source.open(encoding="utf-8", errors="replace") as inp:
        for line in inp:
            total += 1
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 3:
                continue
            nodes.add(parts[0])
            # Only resource objects are entities; literals such as `"Bill"@en` are not made into
            # nodes. Subjects are always resources.
            if is_literal(parts[2]):
                literals += 1
            else:
                nodes.add(parts[2])
    with nodes_target.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["id"])
        for node in sorted(nodes):
            writer.writerow([node])

    keep = total if limit is None else min(limit, total)
    chosen = None
    if keep < total:
        rng = random.Random(seed)
        chosen = set(rng.sample(range(total), keep))

    written = 0
    with source.open(encoding="utf-8", errors="replace") as inp, \
            edges_target.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["subject", "predicate", "object"])
        for index, line in enumerate(inp):
            if chosen is not None and index not in chosen:
                continue
            parts = line.rstrip("\n").split("\t")
            # An edge whose target is a literal has no node to point at.
            if len(parts) != 3 or is_literal(parts[2]):
                continue
            writer.writerow(parts)
            written += 1
    return len(nodes), written, total, literals


def main():
    source = Path(flag("source", str(HERE / "upstream" / "helsinki-multimodel")))
    work = Path(flag("out", str(Path(flag("work", str(paths.WORK_ROOT))) / "hperson")))
    triples = flag("triples", "800000")
    seed = int(flag("seed", "20260831"))
    limit = None if triples == "all" else int(triples)

    processed = source / "data" / "Person_dataset" / "processed_dataset"
    if not processed.exists():
        print("source not found: " + str(processed), file=sys.stderr)
        return 1
    work.mkdir(parents=True, exist_ok=True)

    documents = build_documents(processed / "person.json", work / "person.jsonl")
    print("  documents      {:,} -> {}".format(documents, (work / "person.jsonl").name))

    nodes, kept, total, literals = build_graph(processed / "person.graph",
                                     work / "person_nodes.csv",
                                     work / "person_triples.csv", limit, seed)
    print("  graph nodes    {:,} -> {}".format(nodes, (work / "person_nodes.csv").name))
    print("  literals set aside: {:,} (values, not entities)".format(literals))
    print("  triples        {:,} of {:,} -> {}".format(
        kept, total, (work / "person_triples.csv").name))
    if kept < total:
        print("       sampled uniformly at seed {}; degrees are reported as measured on"
              .format(seed))
        print("       loaded instance rather than as the published ones")
    return 0


if __name__ == "__main__":
    sys.exit(main())
