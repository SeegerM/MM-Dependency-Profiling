#!/usr/bin/env bash
# Prepares the two external systems some experiments need. Neither is required for the core
# pipeline (TPC-H, UniBench, the converted corpora).
#
#   ./scripts/setup-baselines.sh er25        ER'25 (ref-seeker-extractor): build and start the service
#   ./scripts/setup-baselines.sh polypheny   MMSBench-Net: build the Polypheny benchmarking client
#
# Both checkouts stay unmodified in git terms except for the one documented Polypheny patch,
# which is applied only if it is not already present.
set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

case "${1:-}" in
  er25)
    docker compose -p ref-seeker-extractor \
      -f upstream/ref-seeker-extractor/docker-compose.yml \
      -f baselines/er25/docker-compose.local-build.yml \
      up -d --build ref-seeker-extractor mariadb
    echo "ER'25 service on http://localhost:8001, its MariaDB meta level on localhost:8002" ;;

  polypheny)
    patch=baselines/polypheny/dump-runtime-classpath.patch
    if git -C "$POLY_HOME" apply --check "$MM_REPO/$patch" 2>/dev/null; then
      git -C "$POLY_HOME" apply "$MM_REPO/$patch"
    elif ! git -C "$POLY_HOME" apply --reverse --check "$MM_REPO/$patch" 2>/dev/null; then
      echo "cannot apply $patch to $POLY_HOME" >&2; exit 1
    fi
    (cd "$POLY_HOME" && ./gradlew -q classes dumpRuntimeClasspath)
    echo "built $POLY_HOME; core/adapters/build.sh now compiles the MMSBench-Net loader" ;;

  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
