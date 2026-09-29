# ucc — partial UCC discovery (HyUCC)

HyUCC is the `upstream/HyUCC` submodule, branch `partial`, version `1.2-Partial`.

## Does it have the same partial implementation as HyFD? Yes

| | HyFD | HyUCC |
|---|---|---|
| interface | `RelaxedFunctionalDependencyAlgorithm` | `RelaxedUniqueColumnCombinationAlgorithm` |
| version | `1.2-Partial` | `1.2-Partial` |
| threshold identifier | `THRESHOLD` | `THRESHOLD` |
| violation budget | `numRecords - numRecords * threshold` | `numRecords - numRecords * threshold` |
| result type | `RelaxedFunctionalDependency` | `RelaxedUniqueColumnCombination` |
| degree on each result | yes | yes |
| `WRITE_VIOLATIONS` | yes | yes |

Identical relaxation model, and identical to SPIND's `(1 - ρ)·|L|`. All three dependency types
therefore relax on one axis, which is the symmetry the paper's conclusion asks for.

## Multi-model capability — yes, same bridge

Runs against all three stores through `core/adapters/MetanomeBridge`, unmodified.

```
./ucc/run-multimodel.sh postgres public customer 1.0
./ucc/run-multimodel.sh mongo    shop   orders   1.0
./ucc/run-multimodel.sh neo4j    neo4j  Part     1.0
./ucc/run-multimodel.sh file     <path.csv> -    1.0
```

| store | source | extractors | UCCs | arities | any unary key |
|---|---|---|---|---|---|
| PostgreSQL | `public.customer` | 8 | 7 | 1:4, 2:3 | yes |
| Neo4j | `Supplier` | 6 | 4 | 1:4 | yes |
| Neo4j | `Part` | 8 | 4 | 1:2, 5:2 | yes |
| MongoDB | `shop.orders` | 17 | 69 | 2:2, 3:29, 4:20, 5:11, 6:5, 7:2 | **no** |

## The document store finds no key at all

This was predicted from `docs/findings.md` and is now measured. Over the tabularized view of
`shop.orders`, HyUCC reports **69 UCCs and not one of them unary**: the collection's own primary
key `o_orderkey` appears only in combination with line-item attributes.

Exporting the same documents at document scope — one row per order, array dropped — and running the
same algorithm:

```
array-item scope :  69 UCCs, minimum arity 2, o_orderkey is not a key
document scope   :  10 UCCs, [o_orderkey] is the unary key
```

Same data, same algorithm, opposite answers about the primary key. Exploding an embedded array
multiplies every parent row, which destroys the uniqueness of every parent-level key.

Two consequences:

- Relational UCC discovery **cannot** be reused on a tabularized document store. The extractor's
  scope has to travel with the data, or keys are silently lost.
- This propagates into inter-model FDs. A UCC on the referenced side is what makes a join-induced
  scope functional (`docs/composite-scope.md`); if the UCC is missed, the join is not recognised as
  functional and the inter-model FD is never proposed.
