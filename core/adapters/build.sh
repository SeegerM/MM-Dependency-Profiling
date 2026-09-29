#!/usr/bin/env bash
# Shared classpath + build for everything that bridges SPIND extractors into Metanome algorithms.
# Source this from the repo root (it does not cd); it exports MM_CP and COMS_CP.
source config/env.sh

# Aliases used by several drivers.
SPIND_W="$SPIND_HOME"; HYFD_W="$HYFD_HOME"; HYUCC_W="$HYUCC_HOME"
REPO_W="$MM_REPO"

# SPIND first, and Metanome's PostgreSQL 9.3 driver filtered out: it predates SCRAM
# authentication and fails against modern servers.
metanome_cp() { tr "$CPSEP" '\n' < "$1/cp.txt" | grep -vi 'postgresql.9\.3' | paste -sd "$CPSEP" -; }

MM_CP="$(cpjoin "$REPO_W/core/adapters/build" "$REPO_W/core/metalevel/build" "$REPO_W/fd/build" \
  "$REPO_W/ind/build" "$SPIND_W/target/classes" "$(cat "$SPIND_HOME/cp.txt")" \
  "$HYFD_W/target/classes" "$(metanome_cp "$HYFD_HOME")" \
  "$HYUCC_W/target/classes" "$(metanome_cp "$HYUCC_HOME")")"
export MM_CP

# The MMSBench-Net sources need the optional Polypheny benchmarking client; they are skipped when
# it is not built.
COMS_CP=""
if [ -d "${POLY_HOME:-}/build/classes/java/main" ] && [ -f "${POLY_HOME:-}/build/runtime-cp.txt" ]; then
  POLY_W="$POLY_HOME"
  COMS_CP="$(cpjoin "$POLY_W/build/classes/java/main" "$POLY_W/build/resources/main" "$(cat "$POLY_HOME/build/runtime-cp.txt")")"
fi
export COMS_CP

mkdir -p core/adapters/build core/metalevel/build fd/build ind/build
javac -nowarn -cp "$MM_CP" -d core/adapters/build core/adapters/src/*.java
javac -nowarn -cp "$MM_CP" -d core/metalevel/build   $(ls core/metalevel/src/*.java | grep -v 'Coms\.java$')
if [ -n "$COMS_CP" ]; then
  # Separate classpath: combined with MM_CP it overflows the Windows command line.
  javac -nowarn -d core/metalevel/build     -cp "$(cpjoin "$REPO_W/core/metalevel/build" "$SPIND_W/target/classes" "$(cat "$SPIND_HOME/cp.txt")" "$COMS_CP")"     core/metalevel/src/*Coms.java
fi
javac -nowarn -cp "$MM_CP" -d fd/build fd/src/HyFdMultiModel.java fd/src/CompleteMatchDiscovery.java ucc/src/HyUccMultiModel.java
javac -nowarn -cp "$MM_CP" -d ind/build ind/src/*.java
