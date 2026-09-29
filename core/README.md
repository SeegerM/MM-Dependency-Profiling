# core — extraction and the meta level

The only model-specific part of the system. Everything downstream (IND, FD and UCC discovery, the
profile) reads the output of this folder and does not know which data model a value came from.

| folder | contents |
|---|---|
| `adapters/` | `MetanomeBridge` (SPIND sources as Metanome input), `MaskedScope` (`.mscope` file format), shared build script |
| `metalevel/` | `MetaLevelExporter` and the per-instance export entry points |

## metalevel/

`MetaLevelExporter` materializes each store as one observation set per scope. A list-valued graph
property and every document array path define their own scope (`entity@path`), whose matches are
the list elements, so an element is one observation rather than part of a serialized literal.
Relationship types of a graph store are exported as scopes of their own.

Entry points (all take the output directory; the store connections come from the properties file
given with `-Dspind.connections`, see `config/connections.properties.example`):

| class | usage |
|---|---|
| `ExportTpch` | `ExportTpch <outDir> [--with-parent-reference]` |
| `ExportUniBench` | `ExportUniBench <outDir>` |
| `ExportSources` | `ExportSources <specFile> <outDir> [--with-parent-reference] [--flat]` |
| `LoadComs`, `ExportComs` | `LoadComs [networkScale]`, `ExportComs <outDir>` (MMSBench-Net; need the optional Polypheny client) |
| `RelationalConversionBaseline` | `RelationalConversionBaseline <specFile> <outDir>` (independent native-to-relational conversion) |

`ExportSources` reads a spec file with one source per line (`postgres`, `mongo`, `neo4j-label`,
`neo4j-rels`, `union`); the specs used in the paper are in `experiments/datasets/specs/*.sources`.
`--with-parent-reference` adds the owning document's `_id` to each array scope; `--flat` drops the
relationship scopes.

Each export directory contains one CSV per observation set, `catalog.json` (relation, scope, rows,
columns, file for each set) and `types.json` (native type histogram per column). With
`-Dmm.definedness=true` the exporter writes `.mscope` files instead, which carry a definedness mask
per row so that a missing value and an empty string stay distinct.

```bash
source core/adapters/build.sh      # compiles and exports $MM_CP
java -cp "$MM_CP" -Dspind.connections="$MM_REPO/config/connections.properties" \
     ExportTpch "$(mm_path "$WORK_ROOT/mm_tpch/metalevel")"
```

Limitation: values are compared as serialized text, so `42` and `"42"` are the same value.
