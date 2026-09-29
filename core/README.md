# core — extraction and the meta level

The model-specific part of the system, and the only part that is model-specific.

## adapters/
One per data model. Turns a store into observations: a bag of value tuples per extractor.
Currently these live in SPIND (`io/PostgresRelationalInput`, `io/MongoRelationalInput`,
`io/Neo4jRelationalInput`); they move here so that FD discovery can use them too.

Gaps, and where each stands:
- **fixed** — array *container* paths were emitted although they are not leaf paths, and their
  observations were empty, so they were vacuously included in everything;
- **fixed** — only one array per document was exploded and any second was serialized; every array
  path is now its own scope;
- **fixed** — Neo4j list properties were stringified, so `["EA_Sports"]` could never be included in
  a vendor name; every list property is now its own scope;
- *open* — the document family is inferred from a 1,000-document sample in the streaming adapter,
  so completeness is relative to a sampled family rather than to `𝓔(I)` as defined. The exporter
  scans the whole collection, so this affects only the direct-streaming path;
- *open* — values are compared as strings, so `42` and `"42"` are indistinguishable.

## catalog/
The admissible family `𝓔`, materialized: for each extractor, its id, store, scope, arity and
comparison domain. This is the honest denominator for "how many candidates were considered", and
the place where a scope is recorded so a reported coverage can be interpreted.

## metalevel/ — built
`MetaLevelExporter` materializes each store as one observation set **per scope**, plus a catalog.
A list-valued property and an array path each define a scope whose matches are its elements, so an
element is an observation rather than part of a serialized literal. This is where the collection
handling lives, and fixing it here means exact, partial and similarity discovery all inherit it.

Measured against third-party ground truth (`docs/unibench.md`): **100% recall and 100% precision**,
against 76% and 49% when the same stores are streamed directly. Three defects disappear together —
list elements become reachable, the array-container column of nulls that was vacuously included in
everything is no longer emitted, and the document scope regains its own primary key.

```
./ind/run-metalevel.sh 0.5              # export, then discover over the export
```

## join/
Materializes a join-induced scope (`docs/composite-scope.md`) from a discovered IND plus a UCC on
the referenced side. Output is another meta-level observation set, so FD discovery over it needs no
special case. This is what makes an inter-model FD expressible.
