#!/usr/bin/env bash
# Downloads the MusicBrainz schema that experiments/report/musicbrainz_ranking_ablation.py reads
# (column names and SQL types for the ranking features), at the commit the paper used.
set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

COMMIT=4ddcb78e4451d6569c54ff249a5da9926fdb6ebf
SHA256=61962c59d24c7357bab31a943a14eb823af0bf0bca6eb1f303e36adcbb9bc8d2
OUT="$WORK_ROOT/musicbrainz-full/CreateTables-4ddcb78e.sql"

mkdir -p "$(dirname "$OUT")"
curl -sfL -o "$OUT" \
  "https://raw.githubusercontent.com/metabrainz/musicbrainz-server/$COMMIT/admin/sql/CreateTables.sql"
echo "$SHA256  $OUT" | sha256sum -c -
