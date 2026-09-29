# profile — Heterogeneity Profile assembly

Consumes IND, UCC and FD results plus the extractor catalog, and emits the profile of Tab. 1.
`build_profile.py` generates it; see [docs/findings.md](../docs/findings.md) E5 for the numbers and
`paper/tables/profile-*.txt` for the output.

```bash
python profile/build_profile.py <exportDir> <indResultDir> [--ucc=] [--fd=] [--similarity=] [--json=]
```

Inputs it needs, and where each comes from:

| component | source |
|---|---|
| schema-level variation | relaxed candidate generation (which extractors were matched, and how) |
| data-level variation | the coverage `ρ` at which each IND holds |
| syntactic variation | Levenshtein / Jaccard over extractor names and values |
| structural variation | FDs, and differing degrees of normalization across stores |
| join candidates | INDs whose referenced side is a UCC (foreign-key condition) |

Set semantics is the right default here: `docs/findings.md` shows it is invariant to how a nested
store is tabularized, whereas bag semantics is not.
