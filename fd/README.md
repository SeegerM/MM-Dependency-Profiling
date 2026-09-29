# fd — partial FD discovery (HyFD-Partial)

HyFD is the `upstream/HyFD` submodule (branch `partial`), version `1.2-Partial`: a Metanome
`RelaxedFunctionalDependencyAlgorithm` with `maxViolations = numRecords * (1 - threshold)` — the
same partiality model SPIND uses. Partial INDs and partial FDs therefore relax on one axis, which
is the symmetry the paper's conclusion asks for.

Integration is deliberately thin: HyFD takes a Metanome `DefaultFileInputGenerator` over CSV, and
meta-level exports are CSV. No modification to HyFD is required.

Open: HyUCC is a separate repo (`HYUCC_HOME`) and is verified working — see `ucc/README.md`.
iff its PLI has no clusters), which suffices for the foreign-key condition in E3 and the join
validity check in E4. N-ary UCCs would need HyUCC [PN17].

Note from `docs/findings.md`: UCC discovery over a *tabularized* document store misses parent-level
keys, so the export fed to UCC discovery has to carry its scope.

## Integration status — verified 2026-08-22

Runs as a library over a meta-level CSV; no modification to HyFD required.

```
$ ./fd/build.sh F:/temp/multimodel/mm_tpch/pg_customer.csv 1.0
FDs       : 46  (263 ms)
   [c_custkey]->c_name#1.0   [c_custkey]->c_address#1.0   ...
```

| check | result |
|---|---|
| builds offline | yes — every SNAPSHOT dependency resolves from the local `.m2` |
| installed artifact | `de.metanome.algorithms.hyfd:HyFD:1.2-Partial` |
| source vs `HEAD` (branch `partial`) | clean; only `target/` differs |
| library API | `new HyFD()` + `setResultReceiver(ResultCache)` + `execute()` |
| partial mode | works — threshold 0.95 yields 47 FDs against 46 at 1.0 |
| result degree | carried on each result (`#1.0`), so partial FDs report coverage |

Blocking nothing. Two things to tidy:

- `target/` is tracked in git (27 dirty `.class` files); it should be ignored.
- `Runner` hardcodes `F:\metaserve\io\data\` and appends timings to the system temp dir. The
  library path above bypasses it, so this only matters if the Runner is used directly.

Companion: UCC discovery is HyUCC, see `ucc/README.md` — same `1.2-Partial` relaxation model, same bridge, all three stores.
`UniqueColumnCombination` result type in this checkout.

## Multi-model ingestion — yes, via the bridge

HyFD reads all three stores through `core/adapters/MetanomeBridge`, with **no change to HyFD**.
See `core/adapters/README.md` for the measurements and the driver conflict.

```
./fd/run-multimodel.sh postgres public customer 1.0
./fd/run-multimodel.sh mongo    shop   orders   1.0
./fd/run-multimodel.sh neo4j    neo4j  Part     1.0
```

One behavioural note: partial mode costs time on wide inputs. `shop.orders` (17 extractors) takes
1.9 s at θ=1.0 and 20.2 s at θ=0.95, because lowering the threshold keeps far more candidate FDs
alive through the lattice traversal. Fewer results (587 vs 735), much more work — the opposite of
the IND side, where the partitioned strategy proved insensitive to ρ. Worth measuring properly
before the paper quotes FD runtimes.
