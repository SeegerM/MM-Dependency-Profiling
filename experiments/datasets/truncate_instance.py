"""Reduces an instance to a size ER'25 can handle, preserving cross-source correspondences.

Used for the ER'25 comparison, which materialises one row per value and cannot handle instances of
several million observations; both systems run on the reduced instance. Truncating each file
independently would destroy the cross-source inclusions, so the reduction is anchored: one source
is truncated and every other source is filtered to what the surviving rows refer to.

  MovieLens        anchored on links: the surviving films decide which TMDB documents, Wikidata
                   entities, ratings and tags are kept.
  Helsinki Patent  anchored on the citation sample: node identifiers are rebuilt from the surviving
                   edges, so the edge load cannot silently lose rows, and the relational tables keep
                   the patents those edges name.
  ListenBrainz     anchored on the listens: the popularity rows and artists kept are the ones the
                   surviving listens name.

usage: truncate_instance.py <sourceDir> <targetDir> <rows>
"""
import csv
import json
import re
import shutil
import sys
from pathlib import Path

csv.field_size_limit(1 << 24)
MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        return next(reader), list(reader)


def write_csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    return len(rows)


def keep_csv(source, target, rows, predicate=None, limit=None):
    """Copies rows that satisfy `predicate`, up to `limit`."""
    header, all_rows = read_csv(source)
    index = {name: i for i, name in enumerate(header)}
    kept = []
    for row in all_rows:
        if predicate is not None and not predicate(row, index):
            continue
        kept.append(row)
        if limit is not None and len(kept) >= limit:
            break
    return write_csv(target, header, kept)


def jsonl(source, target, predicate=None, limit=None):
    kept = 0
    with source.open(encoding="utf-8") as inp, target.open("w", encoding="utf-8") as out:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if predicate is not None and not predicate(record):
                continue
            out.write(line + "\n")
            kept += 1
            if limit is not None and kept >= limit:
                break
    return kept


def reduce_movielens(source, target, rows):
    header, links = read_csv(source / "pg_links.csv")
    index = {name: i for i, name in enumerate(header)}
    links = links[:rows]
    write_csv(target / "pg_links.csv", header, links)
    movie_ids = {row[index["movieId"]] for row in links}
    tmdb_ids = {row[index["tmdbId"]] for row in links if row[index["tmdbId"]]}
    imdb_ids = {row[index["imdbId"]] for row in links if row[index["imdbId"]]}

    keep_csv(source / "pg_movies.csv", target / "pg_movies.csv", rows,
             lambda row, i: row[i["movieId"]] in movie_ids)
    keep_csv(source / "pg_tags.csv", target / "pg_tags.csv", rows,
             lambda row, i: row[i["movieId"]] in movie_ids, limit=rows)
    keep_csv(source / "pg_ratings.csv", target / "pg_ratings.csv", rows,
             lambda row, i: row[i["movieId"]] in movie_ids, limit=rows)
    # Keep the TMDB documents the surviving links point at, not an arbitrary prefix.
    jsonl(source / "mongo_tmdb_movies.json", target / "mongo_tmdb_movies.json",
          lambda record: str(record.get("id")) in tmdb_ids)
    keep_csv(source / "neo4j_wikidata_film.csv", target / "neo4j_wikidata_film.csv", rows,
             lambda row, i: (row[i["imdb_id"]][2:] if row[i["imdb_id"]].startswith("tt")
                             else row[i["imdb_id"]]) in imdb_ids)


def reduce_hpatent(source, target, rows):
    header, citations = read_csv(source / "neo4j_citations.csv")
    index = {name: i for i, name in enumerate(header)}
    citations = citations[:rows]
    write_csv(target / "neo4j_citations.csv", header, citations)
    endpoints = set()
    for row in citations:
        endpoints.add(row[index["citing"]])
        endpoints.add(row[index["cited"]])
    with (target / "neo4j_patent_nodes.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["id"])
        for identifier in sorted(endpoints):
            writer.writerow([identifier])

    # Patents the surviving edges name, so the endpoint inclusions keep their degrees, plus a
    # prefix so the table is not only the cited ones.
    keep_csv(source / "pg_patent.csv", target / "pg_patent.csv", rows,
             lambda row, i: row[i["patent"]] in endpoints, limit=rows)
    header, patents = read_csv(target / "pg_patent.csv")
    pindex = {name: i for i, name in enumerate(header)}
    kept_patents = {row[pindex["patent"]] for row in patents}
    assignees = {row[pindex["assignee"]] for row in patents if row[pindex["assignee"]]}
    keep_csv(source / "pg_inventor.csv", target / "pg_inventor.csv", rows,
             lambda row, i: row[i["patent"]] in kept_patents, limit=rows)
    keep_csv(source / "pg_assignee.csv", target / "pg_assignee.csv", rows,
             lambda row, i: row[i["assignee"]] in assignees, limit=rows)
    shutil.copyfile(source / "pg_class.csv", target / "pg_class.csv")
    shutil.copyfile(source / "pg_category.csv", target / "pg_category.csv")


def reduce_listenbrainz(source, target, rows):
    listens = target / "mongo_listens.json"
    jsonl(source / "mongo_listens.json", listens, limit=rows)
    mbids = set()
    with listens.open(encoding="utf-8") as handle:
        for line in handle:
            mbids.update(MBID.findall(line))
    keep_csv(source / "pg_top_recording.csv", target / "pg_top_recording.csv", rows,
             lambda row, i: row[i["recording_mbid"]] in mbids or row[i["artist_mbid"]] in mbids)
    keep_csv(source / "pg_mlhd_top_recording.csv", target / "pg_mlhd_top_recording.csv", rows,
             lambda row, i: row[i["recording_mbid"]] in mbids or row[i["artist_mbid"]] in mbids)
    # The artist cache is small enough to keep whole, which preserves the relational-to-graph
    # inclusion.
    shutil.copyfile(source / "neo4j_artists.csv", target / "neo4j_artists.csv")


REDUCERS = {
    "pg_links.csv": reduce_movielens,
    "neo4j_citations.csv": reduce_hpatent,
    "mongo_listens.json": reduce_listenbrainz,
}


def main():
    source, target, rows = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    target.mkdir(parents=True, exist_ok=True)
    for marker, reducer in REDUCERS.items():
        if (source / marker).exists():
            reducer(source, target, rows)
            break
    else:
        print("    no reducer knows this instance: %s" % source, file=sys.stderr)
        return 1
    for path in sorted(target.iterdir()):
        if path.is_file() and path.suffix in (".csv", ".json"):
            with path.open(encoding="utf-8", errors="replace") as handle:
                count = sum(1 for _ in handle)
            print("    %-34s %8d" % (path.name, count - (1 if path.suffix == ".csv" else 0)))
    print("    anchored reduction at %d rows -> %s" % (rows, target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
