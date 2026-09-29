"""Prepares the ListenBrainz instance for loading into the three stores.

Each part follows the publisher's format:

  listens             newline-delimited JSON (MongoDB); `track_metadata.additional_info` is nested
                      and varies per submitting client
  popularity tables   CSV keyed by recording and artist MBID (PostgreSQL)
  artist cache        one entity per artist with typed relationships to other sites (Neo4j)

The MusicBrainz identifier joins the three, with no declared constraints between them:

  listens.recording_mbid  ->  top_recording.recording_mbid     document to relational
  listens.artist_mbids    ->  top_recording.artist_mbid        document array to relational
  top_recording.artist_mbid -> Artist.artist_mbid              relational to graph

Listens are truncated by default (the incremental dump has about 4.5M records), so the degrees of
the listen-to-popularity inclusions are measured on the loaded subset.

usage: build_listenbrainz.py [--source=upstream/musicbrainz-listenbrainz-wikidata]
                             [--out=<work>/listenbrainz] [--listens=400000|all]
                             [--artists=200000|all] [--seed=20260829]
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


def head_jsonl(source, target, limit):
    """Copies the first `limit` well-formed records through, unchanged.

    A prefix rather than a random sample: the dump is ordered by time, so a prefix is one
    contiguous stretch of listening activity that keeps a user's listens together.
    """
    written = malformed = 0
    with source.open(encoding="utf-8") as inp, target.open("w", encoding="utf-8") as out:
        for line in inp:
            if limit is not None and written >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            out.write(line + "\n")
            written += 1
    return written, malformed


def write_artists(source, target, limit):
    """Flattens the artist cache to the columns Neo4j's LOAD CSV will read.

    Only the fields that identify the artist and link it elsewhere are kept; the free text and
    per-site URLs would become extractors with a single value each.
    """
    rows = 0
    with source.open(encoding="utf-8") as inp, \
            target.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["artist_mbid", "name", "area", "type", "gender", "wikidata_url"])
        for line in inp:
            if limit is not None and rows >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            data = record.get("artist_data") or {}
            rels = data.get("rels") or {}
            writer.writerow([
                record.get("artist_mbid", ""),
                data.get("name", "") or "",
                data.get("area", "") or "",
                data.get("type", "") or "",
                data.get("gender", "") or "",
                rels.get("wikidata", "") or "",
            ])
            rows += 1
    return rows


def copy_csv(source, target, limit=None):
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


def main():
    source = Path(flag("source", str(HERE / "upstream" / "musicbrainz-listenbrainz-wikidata")))
    out = Path(flag("out", str(paths.WORK_ROOT / "listenbrainz")))
    listens_flag = flag("listens", "400000")
    artists_flag = flag("artists", "200000")
    listens_limit = None if listens_flag == "all" else int(listens_flag)
    artists_limit = None if artists_flag == "all" else int(artists_flag)
    out.mkdir(parents=True, exist_ok=True)

    events = (source / "data" / "listenbrainz-incremental-20260828"
              / "listenbrainz-listens-dump-2642-20260828-000002-incremental"
              / "listens" / "2026" / "8.listens")
    written, malformed = head_jsonl(events, out / "mongo_listens.json", listens_limit)
    print("  %-16s %10d listens (%d malformed skipped)" % ("listens", written, malformed))

    dump = (source / "data" / "listenbrainz-sample"
            / "listenbrainz-sample-dump-20250610-094311" / "lbdump")
    for name in ("top_recording", "mlhd_top_recording"):
        count = copy_csv(dump / "popularity" / (name + ".csv"), out / ("pg_" + name + ".csv"))
        print("  %-16s %10d rows" % (name, count))

    artists = write_artists(dump / "metadata" / "artists_cache.jsonl",
                            out / "neo4j_artists.csv", artists_limit)
    print("  %-16s %10d artists" % ("artists", artists))
    print("written to " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
