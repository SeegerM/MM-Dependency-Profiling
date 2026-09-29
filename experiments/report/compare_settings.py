"""Compares discovery results across data-model layouts of the same data.

Reads the logs written by experiments/run/run_all.sh for each setting and reports INDs, FDs and
UCCs side by side, then attributes every difference to a named attribute where it can. The three
settings are the same values in three layouts:

    rel      seven flat relations in one relational store
    mm       three stores, read at the meta level (one observation set per scope)
    mmjoin   as mm, with the join-induced scope materialized for the array

usage: compare_settings.py [logRoot] [suffix]
"""
import json
import re
import sys
from pathlib import Path

LOG_ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("F:/temp/multimodel/compare")
SUFFIX = sys.argv[2] if len(sys.argv) > 2 else "1.0"

SETTINGS = [
    ("rel", "relational baseline"),
    ("mm", "multi-model, meta level"),
    ("mmjoin", "multi-model + join scope"),
]

# The same link is named differently in each layout, and comparing the sets is only meaningful
# modulo that renaming. A relational schema names the referencing column (ps_partkey, l_orderkey);
# a nested document has no such column at all, so the link is the document identity (_id); a graph
# edge carries the endpoint's own key (target.p_partkey), because an edge references a node rather
# than a column.
ALIAS = {"o_orderkey": "_id", "l_orderkey": "_id",
         "ps_partkey": "p_partkey", "ps_suppkey": "s_suppkey"}


def rename(pairs):
    return {(ALIAS.get(d, d), ALIAS.get(r, r)) for d, r in pairs}


FOUND = re.compile(r"Found (\d+) pINDs at level 1")
COUNT = re.compile(r"^(FDs|UCCs|extractors)\s*:\s*(\d+)")
SET_NAME = re.compile(r"^--- (.+)$")
TRAILING_COMMA = re.compile(r",\s*([}\]])")


def per_set(path):
    """{observation set -> count} from an fd.log or ucc.log."""
    counts, current = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        name = SET_NAME.match(line)
        if name:
            current = name.group(1)
            continue
        match = COUNT.match(line)
        if match and match.group(1) != "extractors" and current:
            counts[current] = int(match.group(2))
    return counts


def inds(path):
    """Unqualified (dependent, referenced) attribute pairs, so layouts are comparable."""
    text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
    pairs = set()
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = ".".join(pind["dependant"]).split(".")[-1]
            for ref in pind["referenced"]:
                pairs.add((dependent, ".".join(ref["attribute"]).split(".")[-1]))
    return pairs


def load(key):
    directory = LOG_ROOT / f"{key}-{SUFFIX}"
    if not directory.exists():
        return None
    fd, ucc = per_set(directory / "fd.log"), per_set(directory / "ucc.log")
    ind_file = directory / "ind-results" / "1-ary_pINDs.json"
    found = FOUND.findall((directory / "ind.log").read_text(encoding="utf-8"))
    return {
        "sets": len(fd),
        "attributes": sum(int(m.group(2)) for m in
                          filter(None, (COUNT.match(l) for l in
                                        (directory / "fd.log").read_text(encoding="utf-8").splitlines()))
                          if m.group(1) == "extractors"),
        "inds": int(found[-1]) if found else 0,
        "ind_pairs": inds(ind_file) if ind_file.exists() else set(),
        "fds": sum(fd.values()), "fd_by_set": fd,
        "uccs": sum(ucc.values()), "ucc_by_set": ucc,
    }


def main():
    loaded = [(key, label, load(key)) for key, label in SETTINGS]
    loaded = [(k, l, d) for k, l, d in loaded if d]

    print(f"threshold {SUFFIX}\n")
    print(f"  {'setting':<28} {'sets':>5} {'attrs':>6} {'INDs':>6} {'FDs':>6} {'UCCs':>6}")
    for _, label, data in loaded:
        print(f"  {label:<28} {data['sets']:>5} {data['attributes']:>6} "
              f"{data['inds']:>6} {data['fds']:>6} {data['uccs']:>6}")

    by_key = {key: data for key, _, data in loaded}
    if "rel" in by_key and "mm" in by_key:
        rel, mm = by_key["rel"], by_key["mm"]
        print("\n  what the nested layout loses:")
        print(f"    FDs  {rel['fds']} -> {mm['fds']}   ({rel['fds'] - mm['fds']:+d})")
        print(f"    UCCs {rel['uccs']} -> {mm['uccs']}  ({rel['uccs'] - mm['uccs']:+d})")
        lost = rename(rel["ind_pairs"]) - rename(mm["ind_pairs"])
        gained = rename(mm["ind_pairs"]) - rename(rel["ind_pairs"])
        print(f"    INDs {rel['inds']} -> {mm['inds']}   ({rel['inds'] - mm['inds']:+d})")
        for pair in sorted(lost):
            print(f"      lost   {pair[0]:<14} -> {pair[1]}")
        for pair in sorted(gained):
            print(f"      gained {pair[0]:<14} -> {pair[1]}")

    if "rel" in by_key and "mmjoin" in by_key:
        rel, join = by_key["rel"], by_key["mmjoin"]
        same = (rel["inds"] == join["inds"] and rel["fds"] == join["fds"]
                and rel["uccs"] == join["uccs"])
        print("\n  materializing the join-induced scope recovers "
              + ("every count exactly" if same else "some but not all counts"))
        print(f"    INDs {join['inds']} vs {rel['inds']},  FDs {join['fds']} vs {rel['fds']},"
              f"  UCCs {join['uccs']} vs {rel['uccs']}")
        renamed = rename(rel["ind_pairs"])
        residual = renamed - rename(join["ind_pairs"])
        extra = rename(join["ind_pairs"]) - renamed
        if not residual and not extra:
            print(f"    the two IND sets are identical once {'/'.join(sorted(ALIAS))} are read "
                  f"as the one key they are ({len(renamed)} distinct attribute pairs)")
        else:
            for pair in sorted(residual):
                print(f"      missing {pair[0]:<14} -> {pair[1]}")
            for pair in sorted(extra):
                print(f"      extra   {pair[0]:<14} -> {pair[1]}")


if __name__ == "__main__":
    main()
