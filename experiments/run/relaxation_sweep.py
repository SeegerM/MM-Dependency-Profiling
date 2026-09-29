"""Sweeps the relaxation threshold for all three dependency types at once.

SPIND, HyFD-Partial and HyUCC-Partial are claimed to relax on the same axis: a violation budget
(1 - theta) * |rows|. That is what makes the three results comparable -- a profile reporting INDs
at rho = 0.9 beside FDs at theta = 0.9 is only coherent if the two numbers mean the same kind of
thing -- so it is worth measuring rather than asserting.

Counting is the wrong test, and the reason is worth stating because the naive reading is
misleading. HyFD and HyUCC report *minimal* dependencies. As the budget grows a smaller
determinant may become valid, which makes its supersets non-minimal and removes them from the
output. The number of reported FDs therefore rises and falls while the set of *valid* FDs grows
monotonically throughout. A sweep that plotted counts would show an artifact of minimality and
call it a property of relaxation.

The right test is implication. Every dependency valid at a tighter threshold must still hold at a
looser one:

  IND   the pair is still present.
  FD    X -> Y at theta_tight has some X' subset of X with X' -> Y at theta_loose.
  UCC   a key at theta_tight contains some key at theta_loose.

Note that Armstrong closure is the wrong instrument here even though it is the obvious one.
Transitivity is unsound for partial dependencies -- X -> Y and Y -> Z each within budget does not
put X -> Z within it -- so a closure-based test would assert implications the partiality model does
not license. Validity is monotone in the budget by definition, and minimal representatives are what
the algorithms emit, so the subset test above is both correct and sufficient.

usage: relaxation_sweep.py <exportDir> [--thresholds=1.0,0.99,...] [--out=<dir>]
  requires MM_CP; run  source core/adapters/build.sh  first.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

REPO = Path(__file__).resolve().parents[2]


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
EXPORT = Path(POSITIONAL[0]) if POSITIONAL else paths.WORK_ROOT / "unibench" / "metalevel"
THRESHOLDS = flag("thresholds", "1.0,0.99,0.95,0.9,0.75,0.5").split(",")
OUT = Path(flag("out", str(paths.WORK_ROOT / "relaxation")))

FOUND_INDS = re.compile(r"Found (\d+) pINDs at level 1")
TRAILING_COMMA = re.compile(r",\s*([}\]])")
METANOME_FD = re.compile(r"^\[(.*)\]->(.*?)(?:#[\d.]+)?$")
METANOME_UCC = re.compile(r"^\[(.*)\](?:#[\d.]+)?$")


def observation_sets():
    entries = json.loads((EXPORT / "catalog.json").read_text(encoding="utf-8"))
    return [(entry["relation"], EXPORT / entry["file"], entry["columns"]) for entry in entries]


def java(main_class, args):
    return subprocess.run(["java", "-Xmx8g", "-cp", os.environ["MM_CP"], main_class] + args,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=REPO)


# ------------------------------------------------------------------------------------- reading

def inds(threshold):
    result_dir = OUT / f"ind-{threshold}"
    java("SpindMetaLevel", [str(EXPORT), threshold, str(OUT / "temp"), str(result_dir)])
    path = result_dir / "1-ary_pINDs.json"
    if not path.exists():
        return set()
    text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
    pairs = set()
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            dependent = f"{relation['relation']}.{'.'.join(pind['dependant'])}"
            for ref in pind["referenced"]:
                pairs.add((dependent, f"{ref['relation']}.{'.'.join(ref['attribute'])}"))
    return pairs


def dump(main_class, threshold, path, name, suffix):
    target = OUT / f"{name}-{threshold}.{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    java(main_class, ["file", str(path), "-", threshold, str(target)])
    if not target.exists():
        return []
    return [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def strip_prefix(token, columns):
    for column in sorted(columns, key=len, reverse=True):
        if token.endswith("." + column):
            return column
    return None


def parse_fds(lines, columns):
    rules = []
    for line in lines:
        match = METANOME_FD.match(line.strip())
        if not match:
            continue
        determinant = [strip_prefix(t.strip(), columns) for t in match.group(1).split(",")
                       if t.strip()]
        dependent = strip_prefix(match.group(2).strip(), columns)
        if dependent is None or None in determinant:
            continue
        rules.append((frozenset(determinant), dependent))
    return rules


def parse_uccs(lines, columns):
    keys = []
    for line in lines:
        match = METANOME_UCC.match(line.strip())
        if not match:
            continue
        combination = [strip_prefix(t.strip(), columns) for t in match.group(1).split(",")
                       if t.strip()]
        if None not in combination and combination:
            keys.append(frozenset(combination))
    return keys


def still_valid(determinant, dependent, looser):
    """Is this FD still valid at the looser threshold, given the looser threshold's minimal set?"""
    return any(other_dependent == dependent and other_determinant <= determinant
               for other_determinant, other_dependent in looser)


# ---------------------------------------------------------------------------------------- main

def main():
    if not os.environ.get("MM_CP"):
        sys.exit("MM_CP is not set: run  source core/adapters/build.sh  first")
    OUT.mkdir(parents=True, exist_ok=True)
    sets = observation_sets()
    usable = [(name, path, columns) for name, path, columns in sets if columns >= 2]

    print(f"export {EXPORT}")
    print(f"{len(sets)} observation sets, {len(sets) - len(usable)} single-column and skipped "
          f"for FD/UCC (no non-trivial dependency exists there)\n")

    columns_of = {}
    for name, path, _ in usable:
        with path.open(encoding="utf-8", newline="") as handle:
            columns_of[name] = (handle.readline().rstrip("\r\n").split(","))

    ind_sets, fd_sets, ucc_sets = {}, {}, {}
    print(f"  {'threshold':>10} {'INDs':>8} {'FDs':>8} {'UCCs':>8}")
    for threshold in THRESHOLDS:
        ind_sets[threshold] = inds(threshold)
        fd_sets[threshold], ucc_sets[threshold] = {}, {}
        for name, path, _ in usable:
            stem = re.sub(r"[^A-Za-z0-9._-]", "_", name)
            fd_sets[threshold][name] = parse_fds(
                dump("HyFdMultiModel", threshold, path, stem, "fds"), columns_of[name])
            ucc_sets[threshold][name] = parse_uccs(
                dump("HyUccMultiModel", threshold, path, stem, "uccs"), columns_of[name])
        print(f"  {threshold:>10} {len(ind_sets[threshold]):>8} "
              f"{sum(len(v) for v in fd_sets[threshold].values()):>8} "
              f"{sum(len(v) for v in ucc_sets[threshold].values()):>8}")

    print("\n  Counts are minimal representatives, so they need not increase: a determinant that")
    print("  becomes valid removes its supersets from the output. Implication is the real test.\n")

    print(f"  {'tighter -> looser':>22} {'INDs':>10} {'FDs':>10} {'UCCs':>10}")
    all_hold = True
    for tight, loose in zip(THRESHOLDS, THRESHOLDS[1:]):
        ind_ok = ind_sets[tight] <= ind_sets[loose]
        fd_total = fd_kept = ucc_total = ucc_kept = 0
        for name in columns_of:
            for determinant, dependent in fd_sets[tight][name]:
                fd_total += 1
                fd_kept += still_valid(determinant, dependent, fd_sets[loose][name])
            for key in ucc_sets[tight][name]:
                ucc_total += 1
                ucc_kept += any(other <= key for other in ucc_sets[loose][name])
        holds = ind_ok and fd_kept == fd_total and ucc_kept == ucc_total
        all_hold &= holds
        print(f"  {tight + ' -> ' + loose:>22} {'ok' if ind_ok else 'BROKEN':>10} "
              f"{f'{fd_kept}/{fd_total}':>10} {f'{ucc_kept}/{ucc_total}':>10}")

    print(f"\n  every dependency valid at a tighter threshold still holds at the looser one: "
          f"{all_hold}")


if __name__ == "__main__":
    main()
