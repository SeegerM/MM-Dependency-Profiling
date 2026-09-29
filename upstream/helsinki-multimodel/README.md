# Helsinki Multi-Model Dataset Repository

Acquired from the University of Helsinki repository on 2026-08-29. The data files are ignored by
Git; `MANIFEST.sha256` records the exact local snapshot.

| dataset | publisher page | archive | compressed bytes | extracted directory |
|---|---|---|---:|---|
| Film | [description](https://www.helsinki.fi/en/researchgroups/unified-database-management-systems-udbms/datasets/film-dataset) | [ZIP](http://udbms.cs.helsinki.fi/assets/DataSet/Film_dataset.zip) | 169,120,312 | `data/Film_dataset` |
| Person | [description](https://www.helsinki.fi/en/researchgroups/unified-database-management-systems-udbms/datasets/person-dataset) | [ZIP](http://udbms.cs.helsinki.fi/assets/DataSet/Person_dataset.zip) | 54,354,035 | `data/Person_dataset` |
| University | [description](https://www.helsinki.fi/en/researchgroups/unified-database-management-systems-udbms/datasets/university-dataset) | [ZIP](http://udbms.cs.helsinki.fi/assets/DataSet/University_dataset.zip) | 257,672,609 | `data/University_dataset` |
| Patent | [description](https://www.helsinki.fi/en/researchgroups/unified-database-management-systems-udbms/datasets/patent-dataset) | [ZIP](http://udbms.cs.helsinki.fi/assets/DataSet/Patent_dataset.zip) | 382,400,354 | `data/Patent_dataset` |

The publisher only serves the archives over HTTP. Verify every archive against `MANIFEST.sha256`
before using it. All four archives were fully decompressed successfully in this snapshot.

The publisher pages and archives do not contain a clear repository-wide license notice. Treat the
files as research inputs and check redistribution rights before publishing a derived bundle.

Initial format validation found 153,134 valid JSON Lines records in `person.json`. The Film archive
contains 115,351 physical lines: 115,347 valid JSON objects, three blank lines, and one malformed
source record (`Fists of Legend`, physical line 101,433). A loader should skip blanks and quarantine
that record instead of assuming the publisher page's older 96,781-row statistic.

The inputs used for the relational/document/graph instances are:

- `data/Film_dataset/processed_dataset/film.imdb.json`
- `data/Film_dataset/processed_dataset/mix.dbpedia.graph`
- `data/Film_dataset/processed_dataset/mix.yago.graph`
- `data/Person_dataset/processed_dataset/person.json`
- `data/Person_dataset/processed_dataset/person.graph`
- `data/University_dataset/processed_dataset/university_scorecard.table`
- `data/University_dataset/processed_dataset/location.graph`
- `data/Patent_dataset/processed_dataset/*.table`
- `data/Patent_dataset/processed_dataset/citation.graph`
