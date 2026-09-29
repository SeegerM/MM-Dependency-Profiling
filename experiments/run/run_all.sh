#!/usr/bin/env bash
# Runs all three discovery algorithms over one observation-set catalog and records the raw output.
#
#   ./experiments/run/run_all.sh <catalogDir> <threshold> <logDir>
#
# The point of taking a catalog directory rather than a store is that the flat relational baseline
# and the multi-model meta-level export are the same kind of input. The only difference between
# the two settings is which catalog is passed, so nothing about the runners can favour either.
#
# INDs are discovered once over the whole catalog, since an IND relates two observation sets.
# FDs and UCCs are discovered per observation set, since both quantify over matches of one scope.
set -uo pipefail
cd "$(dirname "$0")/../.."
source core/adapters/build.sh

CATALOG="${1:?usage: run_all.sh <catalogDir> <threshold> <logDir>}"
THRESHOLD="${2:-1.0}"
LOGS="${3:?usage: run_all.sh <catalogDir> <threshold> <logDir>}"
mkdir -p "$LOGS"

source config/paths.env

echo "=== INDs (SPIND-Partial, rho=$THRESHOLD) over $CATALOG"
java -Xmx8g -cp "$MM_CP" SpindMetaLevel \
     "$CATALOG" "$THRESHOLD" "$WORK_ROOT/compare/temp" "$LOGS/ind-results" \
     < /dev/null > "$LOGS/ind.log" 2>&1
tail -2 "$LOGS/ind.log"

# Carriage returns are stripped: python writes CRLF here and a trailing one invalidates the path.
mapfile -t FILES < <(python experiments/lib/list_export.py "$CATALOG" | tr -d '\r')

: > "$LOGS/fd.log"
: > "$LOGS/ucc.log"
for file in "${FILES[@]}"; do
  name="$(basename "$file")"
  echo "--- $name" | tee -a "$LOGS/fd.log" >> "$LOGS/ucc.log"
  java -Xmx8g -cp "$MM_CP" HyFdMultiModel  file "$file" - "$THRESHOLD" \
       < /dev/null 2>/dev/null | grep -E "^extractors|^FDs"  >> "$LOGS/fd.log"
  java -Xmx8g -cp "$MM_CP" HyUccMultiModel file "$file" - "$THRESHOLD" \
       < /dev/null 2>/dev/null | grep -E "^extractors|^UCCs|^any unary" >> "$LOGS/ucc.log"
done

echo "logs -> $LOGS"
