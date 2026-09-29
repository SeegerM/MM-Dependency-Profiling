# fd — partial FD discovery (HyFD-Partial)

HyFD-Partial is the `upstream/HyFD` submodule (branch `partial`, version `1.2-Partial`), a Metanome
`RelaxedFunctionalDependencyAlgorithm` with the violation budget
`maxViolations = numRecords * (1 - threshold)`, the same partiality model SPIND and HyUCC-Partial
use. It is used as a library through `core/adapters/MetanomeBridge`; HyFD itself is not modified
here.

| file | purpose |
|---|---|
| `src/HyFdMultiModel.java` | runs HyFD-Partial on one source |
| `src/CompleteMatchDiscovery.java` | complete FD/UCC enumeration over `.mscope` files up to an arity bound |
| `run-multimodel.sh` | `HyFdMultiModel` with the shared classpath |
| `run-metalevel.sh` | `HyFdMultiModel` over every observation set of a meta-level export |

## Usage

Build first (`./run.sh build-upstream`, then `./run.sh build` or `source core/adapters/build.sh`).

```bash
# one source: postgres <schema> <table> | file <path.csv> -
./fd/run-multimodel.sh postgres public customer 1.0
./fd/run-multimodel.sh file "$WORK_ROOT/mm_tpch/metalevel/<set>.csv" - 1.0 out.fds

# every observation set of an export (default: $WORK_ROOT/unibench/metalevel)
./fd/run-metalevel.sh 1.0 "$WORK_ROOT/mm_tpch/metalevel"
```

`HyFdMultiModel <postgres|mongo|neo4j|file> <a> <b> [threshold] [dumpFile]` prints the extractor
and FD counts and a capped listing; with `dumpFile` the full result is written one FD per line as
`[lhs,...]->rhs#degree`. Document and graph stores are read from their meta-level export (`file`),
not directly. For a `.mscope` input the run is delegated to `CompleteMatchDiscovery`, which requires
the dump file and enumerates determinants up to `-Dmm.maxArity` (default 3).

`experiments/run/run_all.sh <exportDir> <threshold> <logDir>` runs SPIND, HyFD-Partial and
HyUCC-Partial over one export. The profile reads FD dumps from `<set>.fds` files beside each
observation set, as written by `experiments/run/genuineness_sweep.py` and
`experiments/run/inter_model_fd.py`.

## Limitations

- The reported FDs are discovered at θ = 1; the partial mode (θ < 1) is available but not used for
  them.
- Lowering the threshold increases run time on wide inputs, since more candidate FDs stay alive
  during the lattice traversal.
