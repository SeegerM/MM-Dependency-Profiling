#!/usr/bin/env python3
"""Fetch the MovieLens-bounded Wikidata IMDb/TMDB identifier slice.

The script is append-only and resumable: rerun the same command to continue an interrupted fetch.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "MultiModelDatasetAcquisition/0.1 (research dataset acquisition)"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalise_imdb(raw: str) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("tt"):
        return raw
    try:
        return f"tt{int(raw):07d}"
    except ValueError:
        return None


def load_links(path: Path) -> tuple[list[str], dict[str, dict[str, set[int]]]]:
    by_imdb: dict[str, dict[str, set[int]]] = defaultdict(
        lambda: {"movie_ids": set(), "tmdb_ids": set()}
    )
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            imdb = normalise_imdb(row.get("imdbId", ""))
            if not imdb:
                continue
            by_imdb[imdb]["movie_ids"].add(int(row["movieId"]))
            tmdb = row.get("tmdbId", "").strip()
            if tmdb:
                try:
                    by_imdb[imdb]["tmdb_ids"].add(int(tmdb))
                except ValueError:
                    pass
    return sorted(by_imdb), by_imdb


def make_query(imdb_ids: list[str]) -> str:
    values = " ".join(json.dumps(value) for value in imdb_ids)
    return f"""
SELECT ?item ?imdb ?tmdb WHERE {{
  VALUES ?imdb {{ {values} }}
  ?item wdt:P345 ?imdb .
  OPTIONAL {{ ?item wdt:P4947 ?tmdb . }}
}}
ORDER BY ?imdb ?item ?tmdb
""".strip()


def request_batch(query: str, retries: int = 6) -> list[dict[str, object]]:
    body = urllib.parse.urlencode({"query": query}).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.load(response)
                return payload["results"]["bindings"]
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt + 1 == retries:
                raise
            retry_after = error.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(60.0, 2.0**attempt)
        except (TimeoutError, urllib.error.URLError):
            if attempt + 1 == retries:
                raise
            delay = min(60.0, 2.0**attempt)
        time.sleep(delay)
    raise AssertionError("unreachable")


def atomic_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def binding_value(binding: dict[str, object], name: str) -> str | None:
    field = binding.get(name)
    if not isinstance(field, dict):
        return None
    value = field.get("value")
    return value if isinstance(value, str) else None


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--links", type=Path, default=root / "data" / "ml-32m" / "links.csv")
    parser.add_argument(
        "--output", type=Path, default=root / "data" / "wikidata" / "movie-links.jsonl"
    )
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Stop after this many batches in the current invocation (useful for a smoke test).",
    )
    parser.add_argument("--delay", type=float, default=0.25)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    links = args.links.resolve()
    output = args.output.resolve()
    progress = output.with_suffix(output.suffix + ".progress.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    ids, local = load_links(links)
    source_hash = sha256(links)

    state: dict[str, object]
    if progress.exists():
        state = json.loads(progress.read_text(encoding="utf-8"))
        if state.get("links_sha256") != source_hash:
            raise SystemExit("links.csv changed; move the old output aside before starting a new snapshot")
        # Batch boundaries are not part of the output semantics, so a resumed run may tune the
        # batch size in response to endpoint throttling. next_offset remains the source-of-truth.
        state["batch_size"] = args.batch_size
    else:
        if output.exists() and output.stat().st_size:
            raise SystemExit(f"refusing to append without progress state: {output}")
        state = {
            "links_sha256": source_hash,
            "batch_size": args.batch_size,
            "source_imdb_ids": len(ids),
            "next_offset": 0,
            "rows_written": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_json(progress, state)

    offset = int(state["next_offset"])
    rows_written = int(state["rows_written"])
    batches_this_run = 0
    with output.open("a", encoding="utf-8", newline="\n") as destination:
        while offset < len(ids):
            if args.max_batches is not None and batches_this_run >= args.max_batches:
                break
            batch = ids[offset : offset + args.batch_size]
            bindings = request_batch(make_query(batch))
            for binding in bindings:
                imdb = binding_value(binding, "imdb")
                item_url = binding_value(binding, "item")
                if not imdb or not item_url or imdb not in local:
                    continue
                tmdb = binding_value(binding, "tmdb")
                record = {
                    "wikidata_id": item_url.rsplit("/", 1)[-1],
                    "imdb_id": imdb,
                    "wikidata_tmdb_id": int(tmdb) if tmdb and tmdb.isdigit() else tmdb,
                    "movielens_movie_ids": sorted(local[imdb]["movie_ids"]),
                    "movielens_tmdb_ids": sorted(local[imdb]["tmdb_ids"]),
                }
                destination.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                rows_written += 1
            destination.flush()
            offset += len(batch)
            batches_this_run += 1
            state.update(
                {
                    "next_offset": offset,
                    "rows_written": rows_written,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            atomic_json(progress, state)
            print(f"{offset}/{len(ids)} IMDb IDs; {rows_written} Wikidata rows", flush=True)
            if offset < len(ids):
                time.sleep(args.delay)

    if offset >= len(ids):
        state["completed_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json(progress, state)
        print(f"complete: {rows_written} rows in {output}")
    else:
        print(f"paused at {offset}/{len(ids)}; rerun the same command to resume")
    return 0


if __name__ == "__main__":
    sys.exit(main())
