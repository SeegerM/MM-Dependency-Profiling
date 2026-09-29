#!/usr/bin/env bash
# Installs the Metanome artifacts that HyFD-Partial and HyUCC-Partial compile against into the local
# Maven repository. None of them is on Maven Central: they are 1.2-SNAPSHOT / 0.0.1-SNAPSHOT builds
# of two HPI repositories, so both are cloned at a fixed commit and installed from source.
#
#   ./scripts/install-metanome.sh                 # into ~/.m2
#   MAVEN_REPO=/tmp/m2 ./scripts/install-metanome.sh   # into another local repository
#
# Needed once per machine, before ./run.sh build-upstream. SPIND needs none of this.
set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

METANOME_URL=https://github.com/HPI-Information-Systems/Metanome.git
METANOME_COMMIT=2469eecede87e1dd8bb0979c432a2843bccf4953
ALGORITHMS_URL=https://github.com/HPI-Information-Systems/metanome-algorithms.git
ALGORITHMS_COMMIT=54383fc2d6df31d42687179dba6284330b01e6f9

DEPS="$WORK_ROOT/deps"
MVN=(mvn -q -B -DskipTests)
[ -n "${MAVEN_REPO:-}" ] && MVN+=("-Dmaven.repo.local=$(mm_path "$MAVEN_REPO")")

fetch() {  # fetch <url> <commit> <dir>: a shallow checkout of exactly that commit
  local url="$1" commit="$2" dir="$3"
  if [ ! -d "$dir/.git" ]; then
    git init -q "$dir"
    git -C "$dir" remote add origin "$url"
  fi
  git -C "$dir" fetch -q --depth 1 origin "$commit"
  git -C "$dir" checkout -q --force FETCH_HEAD
}

mkdir -p "$DEPS"
echo "--- Metanome @ ${METANOME_COMMIT:0:7}"
fetch "$METANOME_URL" "$METANOME_COMMIT" "$DEPS/Metanome"
# Only the four modules the algorithms link against, plus whatever they need (-am).
(cd "$DEPS/Metanome" && "${MVN[@]}" install -pl algorithm_integration,backend,algorithm_helper,test_helper -am)

echo "--- metanome-algorithms @ ${ALGORITHMS_COMMIT:0:7} (utils, dao)"
fetch "$ALGORITHMS_URL" "$ALGORITHMS_COMMIT" "$DEPS/metanome-algorithms"
(cd "$DEPS/metanome-algorithms" && "${MVN[@]}" -N install && "${MVN[@]}" install -pl utils,dao)

echo "installed; now run ./run.sh build-upstream"
