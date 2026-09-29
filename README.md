# Multi-Model Dependency Discovery and Heterogeneity Profiling

Code, experiment pipeline and evaluation for

> Dominique Hausler, Marcian Seeger, Meike Klettke, Thorsten Papenbrock.
> **Data Dependency-Based Heterogeneity Profiling in Multi-Model Databases.**
> Submitted to BTW 2027.

Relationships between relational, document and graph stores are rarely declared, and differences
in structure, naming and values hide them from conventional dependency discovery. This repository
implements the paper's approach:

- Every store is lifted into a model-independent representation of **scopes** and **value
  extractors**. A scope says where values are observed: a table, a document collection, an array
  path, a node label, an edge type, or a union or join of those. An extractor says which values.
- Inclusion dependencies (INDs), functional dependencies (FDs) and unique column combinations
  (UCCs) are discovered over that representation, with partial, similarity-based and
  transformation-aware semantics.
- **Union scopes** over populations split across labels or stores are discovered from the
  dependencies that need them.
- The results are aggregated into evidence-backed **Heterogeneity Profiles**.

```
  PostgreSQL          MongoDB            Neo4j
       |                 |                 |
       +-------- core/metalevel -----------+     the only model-specific code: one table per
                         |                       scope, plus a catalog
        +----------------+-----------------+
        |                |                 |
   ind/ SPIND       fd/ HyFD-Partial   ucc/ HyUCC-Partial      forks of the discovery algorithms,
   partial and      partial FDs        partial UCCs            pinned as submodules
   similarity INDs       |
        |                |
   experiments/run/discover_scopes.py        union scopes, proposed on demand
   experiments/run/discover_transforms.py    transformation-aware INDs
   experiments/run/intermodel_pipeline.py    join-induced scopes, inter-model FDs
                         |
              profile/ Heterogeneity Profiles                  typed findings with their evidence
```

All three algorithms relax on the same axis, a violation budget of `(1 - threshold) × |rows|`, so
partial INDs, FDs and UCCs are directly comparable.

## Results at a glance

On UniBench (34 dependencies labelled by a third party), the scope-aware representation and relaxed
validity solve different problems. Only together do they recover everything:

| representation | validity | recall | oracle precision |
|---|---|---|---|
| flat adapter | exact | 24/34 | 47.1% |
| flat adapter | partial, ρ = 0.5 | 26/34 | 49.1% |
| scope-aware | exact | 26/34 | 89.7% |
| scope-aware | partial, ρ = 0.5 | **34/34** | **91.9%** |

- **Union scope discovery:** recovered references rise from 3/9 to 9/9 on MMSBench-Net and from
  31/35 to 35/35 on LDBC SNB, without declared unions.
- **Transformation-aware INDs:**
  - on real data, discovery infers MovieLens's `tt` registry prefix and Helsinki University's
    OPEID = 100 · OPEID6;
  - on TPC-H, it recovers 30 of 44 injected representation changes.

The paper evaluates eleven dataset entries:

- seeded: TPC-H;
- labelled: UniBench;
- converted corpora: IMDB and MusicBrainz, sampled and full, and LDBC SNB;
- a native multi-model benchmark: MMSBench-Net;
- independently published sources: MovieLens, ListenBrainz and Helsinki UDBMS.

`REPRODUCE.md` maps every number in the paper to the script and table that produce it.

## Quick start

Needs Git, Bash (Git Bash on Windows), JDK 21, Maven 3.9, Python 3.10+ and Docker.

```bash
git clone --recurse-submodules <repository-url> multi-model && cd multi-model
python -m pip install -r requirements.txt && python -m nltk.downloader wordnet
cp config/connections.properties.example config/connections.properties
./run.sh metanome && ./run.sh build-upstream && ./run.sh build
./run.sh test
```

This builds everything and runs the regression tests without any data. To reproduce the
evaluation, see [REPRODUCE.md](REPRODUCE.md).

## Repository layout

| path | contents |
|---|---|
| `core/metalevel/` | `MetaLevelExporter`: stores → one table per scope plus a catalog; per-corpus exporters |
| `core/adapters/` | store adapters, the SPIND→Metanome bridge, masked scope files; `build.sh` compiles everything |
| `ind/`, `fd/`, `ucc/` | runners for SPIND, HyFD-Partial and HyUCC-Partial over an export |
| `profile/` | Heterogeneity Profile records (`hp_records.py`), chance-corrected scores (`genuineness.py`), name similarity, profile diffs |
| `experiments/datasets/` | dataset builders, loaders, conversion specs, oracles, source checksums |
| `experiments/run/` | discovery pipelines (union scopes, transformations, inter-model FDs) and verification |
| `experiments/report/` | report scripts; each writes one table under `paper/tables/` |
| `experiments/lib/` | shared helpers, including `paths.py` (reads `config/paths.env`) and the transformation language |
| `baselines/` | local build of the ER'25 baseline and the Polypheny client patch |
| `scripts/` | one-time setup: Metanome snapshots, baselines, MusicBrainz schema |
| `config/` | paths and connection templates; `env.sh` is the shared shell environment |
| `upstream/` | submodules (below) and acquisition notes for the Helsinki, LDBC SNB, MovieLens and MusicBrainz/ListenBrainz corpora |

The algorithms and baselines are git submodules pinned to exact commits, so a clone builds the code
that produced the numbers:

| submodule | branch | role |
|---|---|---|
| `upstream/spind` | `multimodel` | partial and similarity IND discovery ([SPIND](https://github.com/SeegerM/spind)) |
| `upstream/HyFD` | `partial` | partial FD discovery |
| `upstream/HyUCC` | `partial` | partial UCC discovery |
| `upstream/unibench-ind` | `main` | UniBench-IND generator and its labelled oracle |
| `upstream/ref-seeker-extractor` | `main` | the ER'25 baseline |
| `upstream/Polypheny-Simple-Client` | `master` | Polypheny's benchmarking client, which generates MMSBench-Net |

## Acknowledgements

The work of Dominique Hausler is funded by the Deutsche Forschungsgemeinschaft (DFG, German
Research Foundation), project 552691270. The work of Marcian Seeger is supported by the LOEWE
initiative (Hesse, Germany) within the emergenCITY centre.
