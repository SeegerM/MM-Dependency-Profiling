# LDBC Social Network Benchmark, SF0.1 (CsvBasic)

Used as the second natural instance for the union scope discovery experiments.

Source: https://datasets.ldbcouncil.org/snb-interactive-v1/social_network-sf0.1-CsvBasic-LongDateFormatter.tar.zst
(18,369,356 bytes; checksum in experiments/datasets/manifests/ldbc.sha256). Place the archive in
`data/` and extract it there, e.g. `zstd -d` then `tar -xf`, so that
`data/social_network-sf0.1-CsvBasic-LongDateFormatter/{static,dynamic}/*.csv` exists. Then run
`python experiments/datasets/build_ldbc.py`.

Terms: LDBC fair use policies (https://ldbcouncil.org/benchmarks/fair-use-policies); results here
are not official LDBC benchmark results.
