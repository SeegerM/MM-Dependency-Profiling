"""Held-out evaluation of the schema-name ranking rules on corpora they were not developed on.

The rules are imported unchanged from musicbrainz_ranking_ablation.py. Their MusicBrainz-specific
branches (`l_` link tables, `_gid_redirect`, entity vocabulary) do not fire elsewhere, leaving the
generic core: token overlap between the dependent column's name and the referenced table's name.
Each ordering is compared against the null-relative significance score.

usage: name_rules_holdout.py [--work $WORK_ROOT]
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from musicbrainz_ranking_ablation import (  # noqa: E402  -- imported, never redefined
    compatibility, entity_tokens, role_tokens, source_table,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402


def average_precision(ranked, truth, recoverable):
    """Mean precision at each hit. Same definition as the ablation, over keys rather than rows."""
    if not recoverable:
        return float("nan")
    hits, total = 0, 0.0
    for position, key in enumerate(ranked, 1):
        if key in truth:
            hits += 1
            total += hits / position
    return total / recoverable

# (label, genuineness json, ground-truth json)
CASES = [
    ("IMDB (full)", "imdb-full/genuineness.json", "imdb-full/ground_truth.json"),
    ("TPC-H", "mm_tpch/genuineness.json", "mm_tpch/ground_truth.json"),
    # Control: MusicBrainz exports positional column names, so without CreateTables.sql the name
    # rules have nothing to work with.
    ("MusicBrainz (full), no schema file",
     "musicbrainz-full/genuineness-prefiltered.json", "musicbrainz-full/ground_truth.json"),
    # Publisher-assembled instances, whose names come from uncoordinated sources.
    ("MovieLens", "movielens/genuineness.json", "movielens/ground_truth.json"),
    ("Helsinki Patent", "hpatent/genuineness.json", "hpatent/ground_truth.json"),
    ("ListenBrainz", "listenbrainz/genuineness.json", "listenbrainz/ground_truth.json"),
]


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


def split_attribute(qualified):
    """"<relation>.<attribute>" -> (relation, attribute), longest relation wins."""
    head, _, tail = qualified.rpartition(".")
    return head, tail


def name_score(dependent, referenced):
    """The frozen rule: dependent column's role tokens against referenced table's entity tokens."""
    dep_rel, dep_col = split_attribute(dependent)
    ref_rel, _ = split_attribute(referenced)
    left = role_tokens(source_table(dep_rel), dep_col)
    right = entity_tokens(source_table(ref_rel))
    return compatibility(left, right)


def evaluate(ranked, truth):
    """Average precision plus how far a reader reads to reach each recall level."""
    recoverable = len(truth & set(ranked))
    ap = average_precision(ranked, truth, recoverable) if recoverable else float("nan")
    reached, at = 0, {}
    for position, key in enumerate(ranked, 1):
        if key in truth:
            reached += 1
            for level in (0.5, 1.0):
                if level not in at and reached >= level * recoverable:
                    at[level] = position
    return ap, recoverable, at


WORKED = [
    ("neo4j:Crew", "tconst", "neo4j:Title", "IMDB"),
    ("shop.orders@lineitems", "lineitems.l_partkey", "neo4j:Part", "TPC-H"),
    ("pg.artist_alias", "column2", "pg.artist", "MusicBrainz, no schema file"),
]


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    print("Held-out evaluation of the MusicBrainz schema-name ranking rules.")
    print("Rules imported unchanged from musicbrainz_ranking_ablation.py.\n")

    for label, scores_path, oracle_path in CASES:
        scores_file, oracle_file = work / scores_path, work / oracle_path
        if not scores_file.exists() or not oracle_file.exists():
            print(f"{label}: SKIPPED (missing {scores_file.name} or {oracle_file.name})\n")
            continue

        scored = json.loads(scores_file.read_text(encoding="utf-8"))
        payload = json.loads(oracle_file.read_text(encoding="utf-8"))
        oracle = payload.get("expected_inds") or payload["dependencies"]
        truth = {(e["dependent"], e["referenced"]) for e in oracle}

        rows = [{"key": (s["dependent"], s["referenced"]),
                 "sig": s["significance"],
                 "name": name_score(s["dependent"], s["referenced"])} for s in scored]

        nonzero = sum(1 for r in rows if r["name"] > 0)
        print(f"{label}: {len(rows)} candidates, {len(truth)} declared keys, "
              f"{nonzero} candidates with a non-zero name score")

        orders = {
            "significance": lambda r: (-r["sig"],),
            "schema name only": lambda r: (-r["name"],),
            "sig + schema name": lambda r: (-(r["name"] + _norm(rows, r)),),
        }
        print(f"  {'ranking':<20} {'AP':>6} {'50% at':>8} {'100% at':>8}")
        for name, key in orders.items():
            ranked = [r["key"] for r in sorted(rows, key=key)]
            ap, recoverable, at = evaluate(ranked, truth)
            print(f"  {name:<20} {ap:>6.3f} {at.get(0.5, '--'):>8} {at.get(1.0, '--'):>8}"
                  f"   (recoverable {recoverable}/{len(truth)})")
        print()


def _norm(rows, row):
    """Significance scaled to [0,1] so the two features aggregate on one scale."""
    values = [r["sig"] for r in rows]
    low, high = min(values), max(values)
    return 0.0 if high == low else (row["sig"] - low) / (high - low)


def worked_examples():
    print("Why the score is zero -- one candidate per corpus:")
    print()
    print(f"  {'dependent':<40} {'referenced':<14} {'tokens compared':<44} score")
    for dep_rel, dep_col, ref_rel, _ in WORKED:
        left = role_tokens(source_table(dep_rel), dep_col)
        right = entity_tokens(source_table(ref_rel))
        pair = f"{sorted(left)} vs {sorted(right)}"
        print(f"  {dep_rel + '.' + dep_col:<40} {ref_rel:<14} {pair:<44} "
              f"{compatibility(left, right):.2f}")
    print()


if __name__ == "__main__":
    main()
    worked_examples()
