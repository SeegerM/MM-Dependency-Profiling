"""Characterises the naming variation between IMDB's alternative titles and its primary titles.

Every similarity experiment so far has used variation someone injected: ours in
similarity_sweep.py, a third party's corruption script in the clean/dirty instance. Both answer
whether the pipeline uses a similarity measure; neither says anything about how real data varies.

IMDB carries the answer in its own schema. A title's `akas` rows are the names that title is
released under in other regions, so `akas.titleId` pairs each alternative name with its primary
name without any judgement from us. Measuring the edit distance across that pairing describes the
variation a similarity measure would actually have to tolerate here -- and, as it turns out, bounds
what any edit-distance threshold can achieve on it.

usage: imdb_title_variation.py [exportDir] [--sample=200000]
"""
import csv
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
EXPORT = Path(POSITIONAL[0]) if POSITIONAL else paths.WORK_ROOT / "imdb" / "metalevel"
SAMPLE = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--sample=")), "200000"))

csv.field_size_limit(1 << 30)


def bounded_distance(a, b, cap):
    """Levenshtein distance, abandoned once it provably exceeds cap.

    The cap matters: most alternative titles are translations, so the full distance is large and
    computing it exactly for tens of thousands of pairs is wasted work. Anything above the cap is
    reported as the cap, which is all the histogram needs.
    """
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        best = i
        for j, cb in enumerate(b, 1):
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            current.append(value)
            best = min(best, value)
        if best > cap:
            return cap + 1
        previous = current
    return previous[-1]


def ascii_fold(value):
    """Strips diacritics, so 'Carmencita' and 'Cármencita' count as a formatting difference."""
    return "".join(c for c in unicodedata.normalize("NFD", value)
                   if unicodedata.category(c) != "Mn")


def main():
    titles = {}
    with (EXPORT / "neo4j_Title.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("tconst") and row.get("primaryTitle"):
                titles[row["tconst"]] = row["primaryTitle"]
    print(f"{len(titles):,} primary titles")

    cap = 4
    distances = Counter()
    folded_equal = 0
    case_equal = 0
    compared = 0
    with (EXPORT / "pg.title_akas.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            primary = titles.get(row.get("titleId", ""))
            alternative = row.get("title", "")
            if not primary or not alternative:
                continue
            compared += 1
            if compared > SAMPLE:
                break
            if alternative == primary:
                distances[0] += 1
                continue
            if alternative.casefold() == primary.casefold():
                case_equal += 1
            if ascii_fold(alternative).casefold() == ascii_fold(primary).casefold():
                folded_equal += 1
            distances[bounded_distance(alternative, primary, cap)] += 1

    print(f"{compared - 1:,} alternative titles compared with their own primary title\n")
    total = sum(distances.values())
    print(f"  {'edit distance':>16} {'pairs':>10} {'share':>8}  {'cumulative':>10}")
    running = 0
    for distance in range(cap + 2):
        count = distances.get(distance, 0)
        running += count
        label = f">{cap}" if distance == cap + 1 else str(distance)
        print(f"  {label:>16} {count:>10,} {count / total:>7.1%}  {running / total:>9.1%}")

    print(f"\n  identical after case folding only : {case_equal:,}")
    print(f"  identical after also folding accents: {folded_equal:,}")
    reachable = sum(distances.get(d, 0) for d in range(1, cap + 1))
    print(f"\n  {distances.get(0, 0) / total:.1%} of alternative titles are exactly the primary "
          f"title, so equality already finds them.")
    print(f"  {reachable / total:.1%} more are within {cap} edits -- the band a similarity "
          f"threshold can reach.")
    print(f"  {distances.get(cap + 1, 0) / total:.1%} are further away: these are translations, "
          f"and no edit-distance threshold should match them.")


if __name__ == "__main__":
    main()
