#!/usr/bin/env bash
# Shared classpath + build for everything that bridges SPIND extractors into Metanome algorithms.
# Source this; it exports MM_CP.
# Sourced, not executed: $0 belongs to the caller, so this must not cd anywhere. Callers are
# expected to have changed to the repo root already.
source config/paths.env

win() { (cd "$1" && pwd -W); }
SPIND_W=$(win "$SPIND_HOME"); HYFD_W=$(win "$HYFD_HOME"); HYUCC_W=$(win "$HYUCC_HOME")
REPO_W=$(pwd -W)

# SPIND first, and the 2013 PostgreSQL driver that Metanome drags in filtered out. Whichever
# driver appears first wins, and 9.3 predates SCRAM, so it fails against any modern server with
# an error that reads like a server misconfiguration.
metanome_cp() { tr ';' '\n' < "$1/cp.txt" | grep -vi 'postgresql.9\.3' | tr '\n' ';'; }

export MM_CP="$REPO_W/core/adapters/build;$REPO_W/core/metalevel/build;$REPO_W/fd/build;$REPO_W/ind/build;$SPIND_W/target/classes;$(cat "$SPIND_HOME/cp.txt");$HYFD_W/target/classes;$(metanome_cp "$HYFD_HOME");$HYUCC_W/target/classes;$(metanome_cp "$HYUCC_HOME")"

mkdir -p core/adapters/build core/metalevel/build fd/build ind/build
javac -nowarn -cp "$MM_CP" -d core/adapters/build core/adapters/src/MetanomeBridge.java
javac -nowarn -cp "$MM_CP" -d core/metalevel/build core/metalevel/src/*.java
javac -nowarn -cp "$MM_CP" -d fd/build fd/src/HyFdMultiModel.java ucc/src/HyUccMultiModel.java
javac -nowarn -cp "$MM_CP" -d ind/build ind/src/*.java
