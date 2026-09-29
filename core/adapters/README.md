# adapters — one extractor implementation for all algorithms

`MetanomeBridge` presents a SPIND `TableSource` to Metanome algorithms as a `RelationalInput`.
SPIND's readers (`io/PostgresRelationalInput`, `io/MongoRelationalInput`,
`io/Neo4jRelationalInput`, `io/CsvRelationalInput` in `upstream/spind`) already turn tables,
collections and graph labels into rows of strings, so HyFD-Partial and HyUCC-Partial read the same
extractors as SPIND without modification.

| file | purpose |
|---|---|
| `src/MetanomeBridge.java` | SPIND `TableSource` -> Metanome `RelationalInputGenerator` |
| `src/MaskedScope.java` | reader/writer for `.mscope` files: row ordinal, definedness mask, values |
| `src/MaskedScopeRegression.java` | round-trip and malformed-input test for `.mscope` (run by `./run.sh test`) |
| `build.sh` | builds the shared classpath and compiles `core/`, `ind/`, `fd/` and `ucc/` |

## Build

`build.sh` is sourced from the repository root, not executed. It needs the upstream builds
(`./run.sh build-upstream`), exports the classpath as `$MM_CP`, and compiles into
`core/adapters/build`, `core/metalevel/build`, `ind/build` and `fd/build` (the UCC runner is
compiled into `fd/build` too). `./run.sh build` does the same.

```bash
source core/adapters/build.sh
```

The MMSBench-Net loader and export (`*Coms.java`) are compiled only when the Polypheny client
(`POLY_HOME`) has been built; otherwise they are skipped.

## Notes

- Metanome depends on the 2013 PostgreSQL JDBC driver (9.3), which does not support SCRAM
  authentication. `build.sh` puts SPIND's driver first and removes the old one from the classpath.
- Document and graph stores are not read directly: `MetanomeBridge` rejects Mongo and Neo4j
  sources, because the streaming adapters tabularize a collection into one relation and serialize
  list-valued fields. Export them with `core/metalevel` first and read the exported observation
  sets (`file` sources). Relational tables and CSV files can be read directly.
- `.mscope` files are rejected by the bridge; the FD and UCC runners route them to
  `fd/src/CompleteMatchDiscovery.java` instead.
