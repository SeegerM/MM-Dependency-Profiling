# ucc — partial UCC discovery (HyUCC-Partial)

HyUCC-Partial is the `upstream/HyUCC` submodule (branch `partial`, version `1.2-Partial`), a
Metanome `RelaxedUniqueColumnCombinationAlgorithm`. It uses the same relaxation model as
HyFD-Partial (`maxViolations = numRecords - numRecords * threshold`, parameter `THRESHOLD`) and as
SPIND (`(1 - ρ)·|L|`), so INDs, FDs and UCCs relax on one axis. Each result carries its degree.
HyUCC is used as a library through `core/adapters/MetanomeBridge` and is not modified here.

| file | purpose |
|---|---|
| `src/HyUccMultiModel.java` | runs HyUCC-Partial on one source (compiled into `fd/build`) |
| `run-multimodel.sh` | `HyUccMultiModel` with the shared classpath |
| `run-metalevel.sh` | `HyUccMultiModel` over every observation set of a meta-level export |

## Usage

Build first (`./run.sh build-upstream`, then `./run.sh build` or `source core/adapters/build.sh`).

```bash
# one source: postgres <schema> <table> | file <path.csv> -
./ucc/run-multimodel.sh postgres public customer 1.0
./ucc/run-multimodel.sh file "$WORK_ROOT/mm_tpch/metalevel/<set>.csv" - 1.0 out.uccs

# every observation set of an export (default: $WORK_ROOT/unibench/metalevel)
./ucc/run-metalevel.sh 1.0 "$WORK_ROOT/mm_tpch/metalevel"
```

`HyUccMultiModel <postgres|mongo|neo4j|file> <a> <b> [threshold] [dumpFile]` prints the number of
extractors and UCCs, the UCC count per arity, whether any unary key exists, and the minimal-arity
UCCs. With `dumpFile` the full result is written one UCC per line. Document and graph stores are
read from their meta-level export (`file`), not directly; `.mscope` inputs are delegated to
`fd/src/CompleteMatchDiscovery.java`.

## Note on scopes

UCCs must be discovered per scope. If an embedded array is exploded into its parent's rows, every
parent row is repeated and parent-level keys are lost: over a tabularized document collection the
collection's own primary key is no longer unary. The meta-level export keeps the document scope
and each array scope separate, so the document key is found at document scope. Since join-induced
scopes for inter-model FDs require a UCC on the referenced side, a missed key also means a missed
inter-model FD.
