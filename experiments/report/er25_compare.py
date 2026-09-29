"""Scores the ER'25 framework's INDs against the UniBench oracle.

Both systems run on the UniBench containers the ER'25 repository ships at DATA_SIZE=0.01. ER'25
addresses an attribute by (server type, database, datastorage, attribute); `qualify` maps that to
the oracle's qualified names. An embedded document becomes its own datastorage (e.g. `vendor` for
`Order.order_item.vendor`). Neo4j `elementId` inclusions hold by construction of the graph encoding
and are reported separately.

usage: er25_compare.py [--oracle <unibench_oracle.json>] [--inds <tsv from the view>]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402

STORE = {"PostgreSQL": "public", "MongoDB": "unibench", "neo4j": "neo4j:"}

# Datastorages the framework creates that the oracle addresses by their path in the parent.
EMBEDDED = {("MongoDB", "vendor"): "unibench.Order.order_item.vendor"}


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


def qualify(store_type, datastorage, attribute):
    """(server type, datastorage, attribute) -> the qualified name the oracle uses."""
    embedded = EMBEDDED.get((store_type, datastorage))
    if embedded:
        return f"{embedded}.{attribute}"
    prefix = STORE[store_type]
    if store_type == "neo4j":
        return f"{prefix}{datastorage}.{attribute}"
    return f"{prefix}.{datastorage}.{attribute}"


def read_inds(path):
    """The framework's INDs as (dependent, referenced) in oracle naming.

    The framework runs with find_max_ind, so it lists only maximal INDs. Each n-ary IND is expanded
    into the unary projections it entails, since the oracle is unary.
    """
    rows, structural = [], []
    for line in Path(path).read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 8:
            continue
        c_type, _c_db, c_ds, c_attrs, p_type, _p_db, p_ds, p_attrs = parts[:8]
        left = [a.strip() for a in c_attrs.split(",") if a.strip()]
        right = [a.strip() for a in p_attrs.split(",") if a.strip()]
        if len(left) != len(right):
            continue
        for c_attr, p_attr in zip(left, right):
            pair = (qualify(c_type, c_ds, c_attr), qualify(p_type, p_ds, p_attr))
            # elementId inclusions hold by construction of the graph encoding.
            if "elementId" in c_attr or "elementId" in p_attr:
                structural.append(pair)
            else:
                rows.append(pair)
    return rows, structural


def main():
    oracle_path = flag("oracle", str(paths.WORK_ROOT / "unibench/unibench_oracle.json"))
    inds_path = flag("inds", str(paths.WORK_ROOT / "er25/max-inds.tsv"))

    oracle = json.loads(Path(oracle_path).read_text(encoding="utf-8"))["dependencies"]
    truth = {(e["dependent"], e["referenced"]) for e in oracle}
    exact = {(e["dependent"], e["referenced"]) for e in oracle if e["always_holds"]}

    found, structural = read_inds(inds_path)
    unique = set(found)

    hits = unique & truth
    exact_hits = unique & exact
    print("ER'25 framework on UniBench (DATA_SIZE=0.01), scored against the published oracle\n")
    print(f"  reported inclusions          {len(found)}")
    print(f"    structural (elementId)     {len(structural)}  -- true by graph encoding")
    print(f"    remaining, distinct        {len(unique)}")
    print()
    print(f"  oracle, all                  {len(hits)}/{len(truth)}")
    print(f"  oracle, exactly holding      {len(exact_hits)}/{len(exact)}")
    print(f"  oracle precision             {len(hits)}/{len(unique)} "
          f"({len(hits) / len(unique):.1%})" if unique else "")
    print()
    print("  labelled dependencies it does NOT report:")
    for entry in sorted(oracle, key=lambda e: e["label"]):
        pair = (entry["dependent"], entry["referenced"])
        if pair not in unique:
            mark = "" if entry["always_holds"] else "  (does not always hold)"
            print(f"    {entry['label']:<3} {pair[0]} -> {pair[1]}{mark}")
    print()
    print("  reported but unlabelled:")
    for pair in sorted(unique - truth):
        print(f"        {pair[0]} -> {pair[1]}")
    print(SETUP)


# How the baseline was run; meta-level counts come from the framework's own MariaDB after the run.
SETUP = """
Setup
  framework   upstream/ref-seeker-extractor (published implementation, ER 2025)
  built from  baselines/er25/Dockerfile.local (Python pinned to 3.12; the published image on ghcr.io
              is private and `FROM python:3` now resolves to 3.14, where cassandra_driver 3.29.2 fails to build)
  settings    shipped settings.yaml unchanged: find_max_ind true, speed_mode 0,
              pk_metric pk_score_hopf, fk_metric hopf_probability
  instance    its own UniBench containers at DATA_SIZE=0.01
  meta level  12 datastorages, 40 attributes, 20 UACs, 25 unary INDs, 39 maximal INDs

  The foreign-key scoring stage aborts on this environment inside ForeignkeyFinder:
      AttributeError: 'NoneType' object has no attribute 'wup_similarity'
  a WordNet corpus-version mismatch in the name-similarity step. IND discovery, UAC discovery and
  primary-key detection all complete before it, so the inclusion comparison above is unaffected;
  the framework's own FK ranking is not evaluated here."""


if __name__ == "__main__":
    main()
