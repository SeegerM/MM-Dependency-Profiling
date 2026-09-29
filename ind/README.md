# ind — partial IND discovery (SPIND)

SPIND is the `upstream/spind` submodule (branch `multimodel`). This directory holds the glue:
run configurations, result adapters, and anything needed to drive it over meta-level exports.

Relevant properties for this paper:
- partial INDs via a violation budget `maxViolations = |L| * (1 - ρ)`;
- three interchangeable physical strategies (sort-merge, in-memory inverted index, hash
  partitioning) that produce bit-identical results — verified on the multi-model setup;
- similarity INDs exist but are unary-only and disjoint from the partitioned path, which is why
  Fig. 1's `⊆^{0.86}` claim has no experiment behind it yet (E8).

## Integration status — verified 2026-08-22

| check | result |
|---|---|
| builds and installs | yes — `de.jakob-l-m:spind:1.0.0-SNAPSHOT` |
| installed jar current | **only after a rebuild**; see below |
| multi-model adapters in the artifact | Postgres, Mongo, Neo4j all present |
| library API | `new Spind(config).execute()`; results written as JSON + canonical dumps |
| automated tests | none — `SoccerSmokeTest` is a `main()`, and surefire cannot resolve its provider offline |

**Blocker: the work is uncommitted.** All of it — the multi-model adapters, the three physical
strategies, the benchmark harness — lives as 21 modified/untracked files on the unmerged worktree
branch `claude/multi-model-inclusion-discovery-b5b942`. Before this repo can depend on SPIND:

1. commit and merge that branch;
2. `mvn install` from the merged source.

The `.m2` artifact was a year old (Feb 2025, 27 classes) and contained none of it. A rebuild during
this check took it to 88 classes; anyone else building today would silently get the stale one.
