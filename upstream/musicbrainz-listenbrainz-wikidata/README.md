# MusicBrainz + ListenBrainz + Wikidata

Acquisition snapshot started on 2026-08-29. Large source and generated files are ignored by Git.

## Contents

- MusicBrainz: the 171-table, 16.72 GiB corpus at `$DATA_ROOT/Musicbrainz` (see `REPRODUCE.md`);
  it is not stored in this folder.
- ListenBrainz: the official 2025-06-10 sample dump at
  `downloads/listenbrainz-sample-dump-20250610-094311.tar.zst` (246,734,240 bytes). Its SHA-256
  matches the publisher sidecar and `MANIFEST.sha256`.
- ListenBrainz events: the official 2026-08-28 incremental dump at
  `downloads/listenbrainz-listens-dump-2642-20260828-000002-incremental.tar.zst` (259,094,677
  bytes). The extracted 2.73 GB JSONL file contains native nested listen documents and explicit
  artist, release, release-group, and recording MBIDs. Validation found 4,487,451 JSON records,
  zero malformed lines, 361,827 recording MBIDs, and 427,348 non-empty artist-MBID arrays.
- `extract_sample.py`: safe streaming extraction for either Zstandard-compressed tar archive.

The latest listed full ListenBrainz listens dump was 191 GiB compressed when checked on 2026-08-29,
so it was deliberately not downloaded. The representative sample supplies metadata caches and
Parquet relations; the incremental archive supplies the native listen-event JSON needed for
MongoDB loading.

ListenBrainz downloads: <https://data.metabrainz.org/pub/musicbrainz/listenbrainz/sample/>. User
listen data and text is CC0. MusicBrainz core data is CC0, while supplementary tables have separate
terms: <https://musicbrainz.org/doc/About/Data_License>. Wikidata structured data is CC0.

## Extract the ListenBrainz sample

Install `zstandard` in any Python environment, then run:

```bash
python upstream/musicbrainz-listenbrainz-wikidata/extract_sample.py
```

The extractor rejects absolute paths, parent traversal, and archive links. It extracts through a
partial directory and only renames it to `data/listenbrainz-sample` after success.

The event archive uses the same extractor with explicit paths:

```bash
python upstream/musicbrainz-listenbrainz-wikidata/extract_sample.py \n  --archive upstream/musicbrainz-listenbrainz-wikidata/downloads/listenbrainz-listens-dump-2642-20260828-000002-incremental.tar.zst \n  --destination upstream/musicbrainz-listenbrainz-wikidata/data/listenbrainz-incremental-20260828
```

The metadata sample contains MusicBrainz-to-Wikidata URLs in artist relationship objects.
