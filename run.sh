#!/usr/bin/env bash
# Task runner for the artifact. Runs on Linux, macOS and Windows (Git Bash).
#
#   ./run.sh metanome         install the Metanome snapshots HyFD/HyUCC need (once per machine)
#   ./run.sh build-upstream   build SPIND, HyFD-Partial and HyUCC-Partial in place
#   ./run.sh build            compile the adapters, the exporter and the discovery runners
#   ./run.sh test             regression tests (Java and Python); needs only the two builds
#   ./run.sh tables           regenerate every table the paper reports from the runs in WORK_ROOT
#
# Producing the runs themselves (datasets, exports, discovery) is per instance; see REPRODUCE.md.
set -euo pipefail
cd "$(dirname "$0")"
source config/env.sh

case "${1:-help}" in
  metanome)
    bash scripts/install-metanome.sh ;;

  build-upstream)
    # Build each submodule in place and record its classpath. The runners read target/classes
    # directly instead of installing to ~/.m2, so the exact checked-out sources are used.
    # HyFD and HyUCC need the Metanome snapshots first (./run.sh metanome).
    # MVN_OFFLINE=1 builds without network access once every dependency is cached.
    MVN=(mvn -q -B -DskipTests)
    [ -n "${MVN_OFFLINE:-}" ] && MVN+=(-o)
    [ -n "${MAVEN_REPO:-}" ] && MVN+=("-Dmaven.repo.local=$(mm_path "$MAVEN_REPO")")
    for m in "$SPIND_HOME" "$HYFD_HOME" "$HYUCC_HOME"; do
      echo "--- $m"
      (cd "$m" && "${MVN[@]}" compile && "${MVN[@]}" dependency:build-classpath -Dmdep.outputFile=cp.txt)
    done
    echo "built; classpaths in each upstream/*/cp.txt" ;;

  build)
    source core/adapters/build.sh
    echo "compiled into core/*/build, ind/build, fd/build" ;;

  test)
    source core/adapters/build.sh
    echo "--- SPIND: RFC 4180 CSV round trip and fatal parse errors"
    (cd "$SPIND_HOME" && mvn -q -B ${MVN_OFFLINE:+-o} test-compile)
    java -cp "$(cpjoin "$SPIND_HOME/target/test-classes" "$SPIND_HOME/target/classes" "$(cat "$SPIND_HOME/cp.txt")")" \
      io.CsvRelationalInputRegressionTest
    echo "--- masked scope files: value, null mask and occurrence round trip"
    java -cp "$MM_CP" multimodel.adapters.MaskedScopeRegression "$(mm_path "$WORK_ROOT/test/masked-scope")"
    echo "--- observation semantics and the inter-model FD pipeline"
    python -m unittest experiments/test_semantics.py experiments/run/test_intermodel_pipeline.py
    echo "--- transformation language and transformation-aware IND discovery"
    python -m unittest experiments/test_transforms.py
    echo "--- union proposal rule on controlled positive and negative cases"
    python experiments/run/union_proposal_stress.py
    echo "--- union discovery: shape rule vs demand-driven rule (fails if the demand rule errs)"
    python experiments/run/scope_discovery_stress.py       --out="$(mm_path "$WORK_ROOT/test/scope-discovery-stress.txt")" | tail -4 ;;

  tables)
    # Report step only: every script reads finished runs below WORK_ROOT and writes paper/tables/.
    # A script whose inputs are absent says which run is missing and the rest continue.
    mkdir -p paper/tables
    failed=()
    report() {  # scripts that write their own table
      echo "--- $*"
      python "$@" || failed+=("$1")
    }
    report_to() {  # report_to <table> <script>: scripts that print their table
      local table="paper/tables/$1"; shift
      echo "--- $* > $table"
      if python "$@" > "$table.tmp"; then mv "$table.tmp" "$table"; else rm -f "$table.tmp"; failed+=("$1"); fi
    }
    report experiments/report/oracle_manifest.py            # Tab. 4: instances, oracles, recall
    report experiments/report/tpch_threshold.py             # Tab. 4: TPC-H 8/8 at rho = 0.95
    report experiments/report/similarity_recall.py          # Tab. 4 footnote: MovieLens 10/10, Helsinki Person 4/4
    report_to component-baselines.txt experiments/report/component_baselines.py   # Tab. 5
    report_to er25-baseline.txt experiments/report/er25_compare.py                # Tab. 5: ER'25 row
    report experiments/report/coms_components.py            # MMSBench-Net with/without unions
    report experiments/report/er25_coms.py                  #   ER'25 on MMSBench-Net
    report experiments/report/union_candidates.py           #   the proposed union
    report experiments/report/scope_discovery.py            #   discovered unions: 3/9 -> 9/9
    report experiments/report/published_instances.py        # Helsinki citation coverage
    report experiments/report/hp_records_report.py          # Tab. 3 and the 96-finding profile
    report experiments/report/hp_exemplars.py               #   exemplar findings per kind
    report_to musicbrainz-ranking-ablation.txt experiments/report/musicbrainz_ranking_ablation.py  # P@k, 172
    report_to name-rules-holdout.txt experiments/report/name_rules_holdout.py         #   the rules on held-out corpora
    report experiments/report/intermodel_table.py           # inter-model FDs, all instances
    report experiments/report/published_experiments.py      #   ListenBrainz utilisation
    report_to scale-multimodel.txt experiments/report/scale_report.py            # TPC-H scaling series
    report experiments/report/cost_and_arity.py             # full-corpus runtimes
    report experiments/report/er25_timing.py                # ER'25 phase timings
    if [ ${#failed[@]} -gt 0 ]; then
      echo "incomplete: ${failed[*]}" >&2
      exit 1
    fi
    echo "wrote paper/tables/" ;;

  *) sed -n '2,10p' "$0" ;;
esac
