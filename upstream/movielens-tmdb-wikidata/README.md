# MovieLens + TMDB + Wikidata

Acquisition snapshot started on 2026-08-29. Large source and generated files are ignored by Git.

## Contents

- `data/ml-32m`: MovieLens 32M, the stable 2023-10-13 research release: 32,000,204 ratings,
  2,000,072 tags, and 87,585 movies. `links.csv` supplies the IMDb/TMDB oracle.
- `downloads/movie_ids_08_29_2026.json.gz`: TMDB's 2026-08-29 daily movie-ID export. This is a
  newline-delimited ID/adult/popularity export, not full TMDB movie metadata.
- `data/tmdb/movie_ids_08_29_2026.jsonl`: the validated decompressed export: 1,237,527 JSON objects
  and no malformed lines.
- `fetch_wikidata.py`: a resumable, source-bounded Wikidata Query Service extractor. It maps the
  MovieLens IMDb population to Wikidata items and their TMDB IDs without downloading Wikidata's
  global dump.

MovieLens source: <https://grouplens.org/datasets/movielens/32m/>. GroupLens does not generally
permit public redistribution; keep the ignored archive local and review `data/ml-32m/README.txt`.
The four extracted CSVs were checked against GroupLens's published MD5 values:

```text
8f033867bcb4e6be8792b21468b4fa6e  links.csv
0df90835c19151f9d819d0822e190797  movies.csv
cf12b74f9ad4b94a011f079e26d4270a  ratings.csv
963bf4fa4de6b8901868fddd3eb54567  tags.csv
```

TMDB source and format documentation:
<https://developer.themoviedb.org/docs/daily-id-exports>. Full nested movie documents require a
TMDB API credential and are deliberately not fabricated from the ID export.

## Fetch the Wikidata identifier slice

The extractor uses only Python's standard library and writes resumable JSON Lines output:

```bash
python upstream/movielens-tmdb-wikidata/fetch_wikidata.py
```

Output: `data/wikidata/movie-links.jsonl`. The records preserve MovieLens IDs beside Wikidata's
`P345` (IMDb ID) and `P4947` (TMDB movie ID). Wikidata structured data is CC0.

The 2026-08-29 snapshot is complete and recorded in `WIKIDATA_SNAPSHOT.json`: 76,407 valid rows
cover 76,307 of 87,585 source IMDb IDs (87.12%). Wikidata exactly reproduces MovieLens's TMDB ID
for 75,357 IMDb IDs; 545 mapped entities have no Wikidata TMDB value and 405 disagree. Keep those
cases for oracle evaluation rather than silently reconciling them during loading.
