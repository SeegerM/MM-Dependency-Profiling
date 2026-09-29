# ind — partial IND discovery (SPIND)

SPIND is the `upstream/spind` submodule (branch `multimodel`). This folder holds the runners that
drive it over meta-level exports and over the UniBench-IND stores.

SPIND discovers partial INDs with the violation budget `maxViolations = |L| * (1 - ρ)`, the same
relaxation model HyFD-Partial and HyUCC-Partial use. The physical validation strategy (sort-merge,
in-memory inverted index, hash partitioning) is chosen per run and does not change the result.
Similarity INDs are unary only.

| file | purpose |
|---|---|
| `src/SpindMetaLevel.java` | partial INDs over a meta-level export |
| `src/SpindSimilarity.java` | similarity INDs over a meta-level export |
| `src/SpindMultiModel.java` | partial INDs read directly from the UniBench-IND stores |
| `run-metalevel.sh` | exports UniBench-IND (if not yet exported), then runs `SpindMetaLevel` |
| `run-unibench.sh` | runs `SpindMultiModel` |

## Usage

Build first (`./run.sh build-upstream`, then `./run.sh build` or `source core/adapters/build.sh`).

```bash
java -Xmx8g -cp "$MM_CP" SpindMetaLevel <exportDir> <threshold> <tempFolder> <resultFolder> [maxNary]
java -Xmx8g -cp "$MM_CP" SpindSimilarity <exportDir> <threshold> <tau|prefix|normalised|jaccard|hybrid> \
     <tempFolder> <resultFolder>

./ind/run-metalevel.sh [threshold] [--reexport]   # UniBench-IND: $WORK_ROOT/unibench/meta-results-<threshold>
./ind/run-unibench.sh  [threshold]                # UniBench-IND: $WORK_ROOT/unibench/results-<threshold>
```

`SpindMetaLevel` reads the observation sets listed in `<exportDir>/catalog.json`; `maxNary` defaults
to 1 (unary INDs). Optional system properties select the strategy and domain handling:
`-Dspind.typedComparison`, `-Dspind.prePrune`, `-Dspind.fastValidation`, `-Dspind.inMemory`,
`-Dspind.partitioned`, `-Dspind.partitionFromSource`, `-Dspind.chunkThresholdBytes=<n>`.

`SpindSimilarity` takes either an edit-distance radius `tau` or the name of a comparison relation.
Columns with any value longer than `-Dspind.maxValueLength` are excluded from the similarity path;
raise it for long text columns.

The UniBench runners need the UniBench-IND stores running (see `REPRODUCE.md`) and read the
connections from `config/connections.unibench.properties`.

`.mscope` exports are not supported by SPIND; export plain CSV observation sets for IND discovery.
