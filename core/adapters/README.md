# adapters — one extractor implementation, two algorithms

`MetanomeBridge` presents any SPIND `TableSource` to Metanome algorithms as a `RelationalInput`.
SPIND's adapters already turn relational tables, document collections and graph labels into rows of
strings; Metanome asks for the same thing behind five methods. Bridging them means the
model-specific work is written once and HyFD, HyUCC and any other Metanome algorithm inherit all
three data models unchanged.

This is the paper's "extraction is the only model-specific component" claim in executable form:
two independently written discovery algorithms over one extractor implementation, no intermediate
file required.

## Verified 2026-08-22 — HyFD reading each store directly

| store | source | extractors | FDs | time |
|---|---|---|---|---|
| PostgreSQL | `public.customer` | 8 | 46 | 272 ms |
| MongoDB | `shop.orders` | 17 | 735 | 1,873 ms |
| Neo4j | `Part` | 8 | 20 | 1,370 ms |

```
./fd/run-multimodel.sh mongo shop orders 1.0
```

Two cross-checks came out of this:

- **The bridge agrees with the file path.** `public.customer` yields 46 FDs read live from
  PostgreSQL and 46 from the exported CSV — the bridge changes throughput, not results.
- **The empty array-container extractor shows up again.** MongoDB discovery reports
  `[] -> shop.orders.lineitems`, an FD with an empty determinant, which says the column is
  constant — it is all nulls, because the array is consumed by the explosion. The same adapter
  defect that produced 45 of 96 spurious INDs also produces a spurious FD. One fix, both symptoms.

## Dependency conflict, and it is not cosmetic

Metanome pulls `postgresql:9.3-1102-jdbc41` (2013); SPIND uses `42.7.4`. Whichever comes first on
the classpath wins, and the 2013 driver predates SCRAM, so it fails against any modern PostgreSQL
with *"Authentifizierungstyp 10 wird nicht unterstützt"* — an error that reads like a server
misconfiguration rather than a shaded dependency. `run-multimodel.sh` puts SPIND first and drops
the old driver. A real Maven module should express this as an `<exclusion>`.

## Streaming is retired for document and graph sources

`MetanomeBridge.generateNewCopy` now refuses a Mongo or Neo4j source outright:

```
Streaming neo4j:Tag directly is retired: a collection-valued field would be observed as a
serialized literal. Export it first with core/metalevel/MetaLevelExporter and read the
exported observation sets.
```

The streaming adapters tabularize a store into one relation, which serializes list-valued fields
and explodes only the first array. Measured cost on UniBench-IND: 24 points of recall and roughly
half the precision. Relational and file sources are unaffected — they have one scope, so
tabularizing them is correct.

Both algorithms read exported observation sets instead:

```
./ind/run-metalevel.sh 0.5      # export, then partial INDs
./fd/run-metalevel.sh  1.0      # partial FDs over every scope
./ucc/run-metalevel.sh 1.0      # partial UCCs over every scope
```

Running UCC discovery over the export also settles the earlier finding that a tabularized
collection has no unary key: at document scope `unibench.Order` has one again.
