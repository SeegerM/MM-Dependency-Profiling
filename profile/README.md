# profile — Heterogeneity Profile

Builds the Heterogeneity Profile from discovery results: IND results (SPIND), FD and UCC results
(HyFD-Partial, HyUCC-Partial) and the extractor catalog of a meta-level export. Nothing here
discovers dependencies; every entry is read off those results.

| script | purpose |
|---|---|
| `build_profile.py` | aggregate profile: schema-level, data-level, syntactic, structural variation and join candidates |
| `hp_records.py` | the profile as records, one per finding, each with kind, subject, quadrant, evidence, measurement and chance-corrected strength |
| `hp_diff.py` | added, removed and changed records between two record sets |
| `genuineness.py` | chance-corrected strength of candidate inclusions (`gcov`, significance) |
| `name_similarity.py` | classifies each cross-store link by how its extractor names differ |
| `result_kind.py`, `metanome_dumps.py` | shared helpers: result kinds of inclusions, parser for FD/UCC dumps |

## Usage

```bash
python profile/build_profile.py <exportDir> <indResultDir> [--ucc=<uccLog>] [--fd=<fdLog>] \
       [--similarity=<simResultDir>] [--json=<out>]
python profile/hp_records.py <exportDir> <indResultDir> [--rho=1.0] [--similarity=<dir>] \
       [--genuineness=<json>] [--intermodel=<json>] [--u=<float>] [--transforms=<json>] \
       [--jsonl=<out>] [--top=20]
python profile/hp_diff.py <before.jsonl> <after.jsonl> [--quadrant=A.23] [--kind=partial-link] \
       [--changed-only] [--json=<out>]
python profile/genuineness.py <exportDir> <indResultDir> [--sample=200000] [--alpha=0.05] \
       [--prefilter] [--cache=<file>] [--top=25] [--json=<out>]
python profile/name_similarity.py <exportDir> <indResultDir> [--string=0.7] [--lexical=0.7]
```

Inputs: `<exportDir>` is a meta-level export (`catalog.json` plus observation sets) and
`<indResultDir>` the SPIND result folder for it. `--ucc` and `--fd` of `build_profile.py` take the
`ucc.log` / `fd.log` written by `experiments/run/run_all.sh`. `hp_records.py` reads FD and UCC dumps
from `<set>.fds` / `<set>.uccs` files beside each observation set; where they are absent, the record
set simply has no FD- or UCC-derived records.

Notes:

- Coverage is computed under set semantics (over distinct dependent values), which does not depend
  on how a nested store is tabularized.
- Record identities exclude the measured degree, so in `hp_diff.py` a link whose coverage drifts is
  reported as one changed record rather than a removal plus an addition.
- `genuineness.py` scans the whole instance; on large corpora cache that scan with `--cache`, since
  it does not depend on which run is scored. `experiments/run/genuineness_sweep.py` applies the same
  chance correction to FDs (`gpdep`) and UCCs.
- The lexical-semantic name class uses WordNet
  (`python -m nltk.downloader wordnet`); without it that class is not assigned.
