#!/usr/bin/env bash
# Runs HyUCC-Partial over every observation set of a meta-level export.
#   ./ucc/run-metalevel.sh [threshold] [exportDir]
set -uo pipefail
cd "$(dirname "$0")/.."
source core/adapters/build.sh

THRESHOLD="${1:-1.0}"
EXPORT="${2:-$WORK_ROOT/unibench/metalevel}"

# The catalog lists the observation sets; file names alone are lossy, because a relation name is
# sanitised on the way to disk. Carriage returns are stripped because python writes CRLF on Windows.
mapfile -t FILES < <(python experiments/lib/list_export.py "$EXPORT" | tr -d '\r')

for file in "${FILES[@]}"; do
  echo "--- $(basename "$file")"
  java -Xmx8g -cp "$MM_CP" HyUccMultiModel file "$file" - "$THRESHOLD" < /dev/null 2>/dev/null \
    | grep -E "^extractors|^UCCs|^any unary"
done
