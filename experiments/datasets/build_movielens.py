"""Prepares the MovieLens + TMDB + Wikidata instance for loading into the three stores.

The three parts are independently maintained sources, each loaded in its published form:
MovieLens relational CSVs (PostgreSQL), a TMDB newline-delimited JSON export (MongoDB) and a
Wikidata entity graph keyed by Q-identifier (Neo4j). Their correspondences are all imperfect:

  links.tmdbId  ->  tmdb.id            partial (~0.986): TMDB has since deleted or merged films.
  links.imdbId  ->  wikidata.imdb_id   0 under equality: MovieLens writes "0114709", Wikidata
                                       "tt0114709"; holds almost fully under a comparison relation.
  wikidata_tmdb_id -> links.tmdbId     the sources disagree for some films; kept unreconciled,
                                       as the source README asks.

Ratings are capped by default because the 32M-row file makes the FD and UCC passes impractical;
capping only shrinks the dependent side of ratings.movieId.

usage: build_movielens.py [--source=upstream/movielens-tmdb-wikidata]
                          [--out=<work>/movielens] [--ratings=2000000|all]
"""
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "experiments" / "lib"))
import paths  # noqa: E402

csv.field_size_limit(1 << 24)


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def copy_csv(source, target, limit=None):
    """Copies a CSV through, optionally truncated, and returns the row count written."""
    written = 0
    with source.open(newline="", encoding="utf-8") as inp, \
            target.open("w", newline="", encoding="utf-8") as out:
        reader = csv.reader(inp)
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(next(reader))
        for row in reader:
            if limit is not None and written >= limit:
                break
            writer.writerow(row)
            written += 1
    return written


def write_wikidata(source, target):
    """Flattens the Wikidata slice to a CSV Neo4j's LOAD CSV can read.

    The two MovieLens identifier fields are arrays (one Wikidata item can carry several). They are
    joined with ';', which no identifier contains, and split again on load into list properties.
    """
    rows = 0
    with source.open(encoding="utf-8") as inp, \
            target.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["wikidata_id", "imdb_id", "wikidata_tmdb_id",
                         "movielens_movie_ids", "movielens_tmdb_ids"])
        for line in inp:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            tmdb = record.get("wikidata_tmdb_id")
            writer.writerow([
                record.get("wikidata_id", ""),
                record.get("imdb_id", ""),
                "" if tmdb is None else tmdb,
                ";".join(str(v) for v in record.get("movielens_movie_ids", [])),
                ";".join(str(v) for v in record.get("movielens_tmdb_ids", [])),
            ])
            rows += 1
    return rows


def main():
    source = Path(flag("source", str(HERE / "upstream" / "movielens-tmdb-wikidata")))
    out = Path(flag("out", str(paths.WORK_ROOT / "movielens")))
    ratings_flag = flag("ratings", "2000000")
    limit = None if ratings_flag == "all" else int(ratings_flag)
    out.mkdir(parents=True, exist_ok=True)

    ml = source / "data" / "ml-32m"
    counts = {}
    for name in ("movies", "links", "tags"):
        counts[name] = copy_csv(ml / (name + ".csv"), out / ("pg_" + name + ".csv"))
    if limit != 0:
        counts["ratings"] = copy_csv(ml / "ratings.csv", out / "pg_ratings.csv", limit)
    else:
        (out / "pg_ratings.csv").write_text("userId,movieId,rating,timestamp\n", encoding="utf-8")
        counts["ratings"] = 0

    # The TMDB export is already newline-delimited JSON for mongoimport; copy it unchanged.
    target = out / "mongo_tmdb_movies.json"
    source_jsonl = source / "data" / "tmdb" / "movie_ids_08_29_2026.jsonl"
    documents = 0
    with source_jsonl.open(encoding="utf-8") as inp, target.open("w", encoding="utf-8") as handle:
        for line in inp:
            if line.strip():
                handle.write(line if line.endswith("\n") else line + "\n")
                documents += 1
    counts["tmdb documents"] = documents

    counts["wikidata nodes"] = write_wikidata(
        source / "data" / "wikidata" / "movie-links.jsonl", out / "neo4j_wikidata_film.csv")

    for name, value in counts.items():
        print("  %-16s %10d" % (name, value))
    print("written to " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
