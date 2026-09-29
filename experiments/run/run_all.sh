#!/usr/bin/env bash
# Runs all three discovery algorithms over one observation-set catalog and records the raw output.
#
#   ./experiments/run/run_all.sh <catalogDir> <threshold> <logDir>
#
# The flat relational baseline and the multi-model meta-level export are both catalog directories,
# so the two settings differ only in which catalog is passed.
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

source config/env.sh

echo "=== INDs (SPIND-Partial, rho=$THRESHOLD) over $CATALOG"
java -Xmx8g -cp "$MM_CP" SpindMetaLevel \
     "$CATALOG" "$THRESHOLD" "$WORK_ROOT/compare/temp" "$LOGS/ind-results" \
     < /dev/null > "$LOGS/ind.log" 2>&1
tail -2 "$LOGS/ind.log"

# Carriage returns are stripped: python writes CRLF here and a trailing one invalidates the path.
mapfile -t FILES < <(python experiments/lib/list_export.py "$CATALOG" | tr -d '\r')

: > "$LOGS/fd.log"
: > "$LOGS/ucc.log"
# HEAP (GB) is settable because the largest IMDB scopes exceed 8g. A non-zero exit status is
# recorded in the log, so an unfinished scope is not mistaken for one with no results.
HEAP="${HEAP:-8}"
for file in "${FILES[@]}"; do
  name="$(basename "$file")"
  echo "--- $name" | tee -a "$LOGS/fd.log" >> "$LOGS/ucc.log"
  for pair in "HyFdMultiModel fd ^extractors|^FDs" "HyUccMultiModel ucc ^extractors|^UCCs|^any unary"; do
    set -- $pair
    java "-Xmx${HEAP}g" -cp "$MM_CP" "$1" file "$file" - "$THRESHOLD" \
         < /dev/null 2>"$LOGS/$2-stderr.log" | grep -E "$3" >> "$LOGS/$2.log"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
      echo "DID NOT COMPLETE (exit ${PIPESTATUS[0]}, heap ${HEAP}g): $(tail -1 "$LOGS/$2-stderr.log")" \
        >> "$LOGS/$2.log"
    fi
  done
done

echo "logs -> $LOGS"
