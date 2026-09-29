"""Chance-corrected strength for the three dependency classes the profile reports.

Following CPFD, a dependency's score is corrected by subtracting what it would score by chance:

    gpdep(X -> Y) = pdep(X -> Y) - E[pdep(X -> Y)]

The null: every value is an independent draw from the instance's own value distribution,
restricted to its comparison domain. Values are not independent in practice, so sig is used as a
ranking score, not as a calibrated test.

For INDs, a value lands in supp(R) with probability q (R's cell share), so a dependent with d
distinct values is contained by chance with probability q^d:

    gcov = (cov - q) / (1 - q)         coverage beyond chance (clamped at zero)
    sig  = -log10 P[Bin(d, q) >= k]    how unlikely the observation is

On an exact run cov = 1 gives gcov = 1 for every candidate; sig still separates candidates
because it depends on d. pdep/gpdep for FDs are provided as library functions.

usage: genuineness.py <exportDir> <indResultDir> [--sample=200000] [--alpha=0.05]
                      [--prefilter] [--cache=<file>] [--top=25] [--json=<out>]
"""
import csv
import json
import math
import pickle
import re
import sys
import zlib
from pathlib import Path

TRAILING_COMMA = re.compile(r",\s*([}\]])")


def flag(name, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


# ------------------------------------------------------------------- the background distribution

class Background:
    """The database's own value distribution, sampled by hashing.

    A value is sampled when its hash falls below a threshold, so the sample is a uniform subset of
    the distinct values and membership depends only on the value. One scan thus yields each sampled
    value's instance-wide count, the columns containing it, and support overlaps.

    The threshold halves whenever the sample outgrows its cap; a value that survives a halving was
    below every earlier threshold, so its count is complete.
    """

    def __init__(self, cap):
        self.cap = cap
        self.limit = 1 << 32
        self.count = {}          # sampled value -> cells in the whole instance holding it
        self.support = {}        # column -> {sampled value it contains -> cells of this column}
        self.cells = 0           # non-empty cells, sampled or not
        self.weight = 0

    @staticmethod
    def _hash(value):
        return zlib.crc32(value.encode("utf-8", "replace"))

    def observe(self, column, value):
        self.cells += 1
        if self._hash(value) >= self.limit:
            return
        seen = self.count.get(value)
        if seen is not None:
            self.count[value] = seen + 1
        else:
            self.count[value] = 1
            if len(self.count) > self.cap:
                self._shrink()
                if value not in self.count:
                    return
        local = self.support.setdefault(column, {})
        local[value] = local.get(value, 0) + 1

    def _shrink(self):
        self.limit //= 2
        self.count = {v: c for v, c in self.count.items() if self._hash(v) < self.limit}

    def finish(self):
        """Drops sampled values evicted after a column recorded them, and totals the weights."""
        for column, local in self.support.items():
            self.support[column] = {v: c for v, c in local.items() if v in self.count}
        self.weight = sum(self.count.values())

    @staticmethod
    def _domain(value):
        """The comparison domain a value belongs to: "numeric" or "text", matching the two domains
        the discovery core distinguishes when pruning candidates."""
        try:
            float(value)
            return "numeric"
        except ValueError:
            return "text"

    def _domains(self):
        """Per-domain sampled weight, and each column's own domain. Computed on first use, so
        pickled backgrounds without these attributes still work."""
        if not hasattr(self, "domain_weight"):
            kind = {value: self._domain(value) for value in self.count}
            self.domain_weight = {"numeric": 0, "text": 0}
            for value, cells in self.count.items():
                self.domain_weight[kind[value]] += cells
            self.column_domain = {}
            for column, local in self.support.items():
                kinds = {kind[v] for v in local}
                # A column with mixed domains is scored against the whole instance.
                self.column_domain[column] = kinds.pop() if len(kinds) == 1 else None
        return self.domain_weight, self.column_domain

    def _domain_distinct(self):
        """How many distinct sampled values each comparison domain holds (the denominator of the
        distinct-support null). Computed on first use, like _domains()."""
        if not hasattr(self, "domain_distinct"):
            kind = {value: self._domain(value) for value in self.count}
            self.domain_distinct = {"numeric": 0, "text": 0}
            for value in self.count:
                self.domain_distinct[kind[value]] += 1
        return self.domain_distinct

    def support_share(self, column, exclude=None, conditioned=True):
        """q over distinct values rather than cells: how much of the domain the support covers.

        Unlike share(), a heavily repeated column does not dominate the estimate.
        """
        values = self.support.get(column)
        if not values:
            return 0.0
        drop = set(self.support.get(exclude, ())) if exclude else set()
        if conditioned:
            _, column_domain = self._domains()
            domain = column_domain.get(column)
            if domain:
                pool = self._domain_distinct()[domain]
                inside = sum(1 for v in values
                             if v not in drop and self._domain(v) == domain)
                return min(1.0, inside / pool) if pool else 0.0
        pool = len(self.count) - len(drop)
        inside = sum(1 for v in values if v not in drop)
        return min(1.0, inside / pool) if pool > 0 else 0.0

    def share(self, column, exclude=None, floor=0.0, conditioned=True):
        """q: the chance a draw from the instance lands in this column's support.

        The dependent's own cells (`exclude`) are removed from the pool, since they would otherwise
        raise the null for the very pair being tested. With `conditioned`, the pool is restricted to
        the referenced column's comparison domain, so unrelated values in another domain do not
        change the score.

        `floor` is the referenced column's exact non-null share; it keeps a support too small to be
        sampled from getting q = 0 and an infinite significance.
        """
        drop = self.support.get(exclude, {}) if exclude else {}
        values = self.support.get(column)
        pool = self.weight
        if conditioned:
            domain_weight, column_domain = self._domains()
            domain = column_domain.get(column)
            if domain and domain_weight[domain] > 0:
                pool = domain_weight[domain]
                drop = {v: c for v, c in drop.items() if self._domain(v) == domain}
                # Rescale the floor from a share of all cells to a share of this pool.
                floor *= self.weight / pool
        weight = pool - sum(drop.values())
        estimate = 0.0
        if values and weight > 0:
            estimate = sum(self.count[v] - drop.get(v, 0) for v in values) / weight
        return min(max(estimate, floor), 1.0)

    def overlap(self, left, right):
        """Estimated set coverage |supp(L) & supp(R)| / |supp(L)|, with the sample size behind it.

        The sample is uniform over distinct values, so the estimate is unbiased; the sample size
        indicates how noisy it is.
        """
        sampled = self.support.get(left)
        if not sampled:
            return None, 0
        other = self.support.get(right, {})
        return sum(1 for v in sampled if v in other) / len(sampled), len(sampled)


# ------------------------------------------------------------------------------- the measures

def log10_binomial_tail(trials, probability, hits):
    """log10 P[Bin(trials, probability) >= hits], as a bound when the exact sum would be too long.

    Exact containment gives q^d directly. Partial coverage uses a Chernoff bound, which overstates
    the probability and so understates the significance.
    """
    if probability <= 0.0:
        return -math.inf if hits > 0 else 0.0
    if probability >= 1.0 or hits <= 0:
        return 0.0
    if hits >= trials:
        return trials * math.log10(probability)
    rate = hits / trials
    if rate <= probability:
        return 0.0
    divergence = (rate * math.log(rate / probability)
                  + (1 - rate) * math.log((1 - rate) / (1 - probability)))
    return -trials * divergence / math.log(10)


def inclusion_genuineness(coverage, distinct, share):
    """(gcov, significance) for one candidate inclusion."""
    gcov = (coverage - share) / (1 - share) if share < 1.0 else 0.0
    hits = round(coverage * distinct)
    return max(gcov, 0.0), -log10_binomial_tail(distinct, share, hits)


def pdep(groups, total):
    """CPFD's pdep from group sizes: the chance two random matches agree."""
    return sum((size / total) ** 2 for size in groups) if total else 0.0


def gpdep(determinant_groups, joint_groups, dependent_groups, total):
    """(pdep, E[pdep], gpdep) for one functional dependency.

    E[pdep] = pdep(Y) + (|X| - 1)/(n - 1) * (1 - pdep(Y)), which grows with the number of
    determinant groups.
    """
    if total <= 1:
        return 0.0, 0.0, 0.0
    observed = sum(size / total * pdep(inner, size)
                   for size, inner in zip(determinant_groups, joint_groups))
    baseline = pdep(dependent_groups, total)
    expected = baseline + (len(determinant_groups) - 1) / (total - 1) * (1 - baseline)
    return observed, expected, observed - expected


# --------------------------------------------------------------------------------------- the cut

def cliff(scores):
    """HoPF's parameter-free cut: the largest gap between neighbouring scores.

    Reported for comparison; `bonferroni` is preferred.
    """
    ordered = sorted(scores, reverse=True)
    if len(ordered) < 2:
        return len(ordered)
    return max((ordered[i] - ordered[i + 1], i + 1) for i in range(len(ordered) - 1))[1]


def bonferroni(tested, alpha):
    """The significance a candidate must reach when `tested` of them were examined.

    The level is divided by the number of candidates tested, not the number that survived.
    """
    return -math.log10(alpha / tested) if tested > 0 else 0.0


# ------------------------------------------------------------------------------------- the corpus

def read_catalog(export):
    return json.loads((export / "catalog.json").read_text(encoding="utf-8"))


def scan_cached(export, cap, cache):
    """scan(), cached in a pickle. The result depends only on the export, and the scan is slow on
    large corpora."""
    path = Path(cache) if cache else None
    if path and path.exists():
        # A cache pickled with this file as the entry point refers to __main__.Background.
        main = sys.modules["__main__"]
        if not hasattr(main, "Background"):
            main.Background = Background
        with path.open("rb") as handle:
            stats, background = pickle.load(handle)
        if background.cap == cap:
            print(f"background read from {path}")
            return stats, background
    stats, background = scan(export, cap)
    if path:
        with path.open("wb") as handle:
            pickle.dump((stats, background), handle, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"background written to {path}")
    return stats, background


def scan(export, cap):
    """One pass over every observation set: per-column counts, and the background sample."""
    csv.field_size_limit(10_000_000)
    background = Background(cap)
    stats = {}
    for entry in read_catalog(export):
        with (export / entry["file"]).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            names = [f"{entry['relation']}.{c}" for c in columns]
            distinct = {n: set() for n in names}
            filled = dict.fromkeys(names, 0)
            rows = 0
            for row in reader:
                rows += 1
                for column, name in zip(columns, names):
                    value = row.get(column)
                    if value:
                        distinct[name].add(value)
                        filled[name] += 1
                        background.observe(name, value)
        for name in names:
            stats[name] = {"distinct": len(distinct[name]), "filled": filled[name], "rows": rows,
                           "relation": entry["relation"], "store": entry.get("store", "relational")}
        del distinct
    background.finish()
    return stats, background


def read_inds(result_dir):
    """(dependent, referenced, reported degree) for every discovered inclusion."""
    found = []
    for path in sorted(Path(result_dir).glob("*ary_pINDs.json")):
        text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
        for relation in json.loads(text):
            for pind in relation.get("pINDs", []):
                dependent = f"{relation['relation']}.{'.'.join(pind['dependant'])}"
                for ref in pind["referenced"]:
                    found.append((dependent, f"{ref['relation']}.{'.'.join(ref['attribute'])}",
                                  ref.get("partialDegree", 1.0)))
    return found


def prefilter(candidates, stats):
    """Keeps candidates with a non-empty dependent and a referenced side that is a key.

    These rules discard candidates disqualified by definition; the score ranks the rest.
    """
    kept = []
    for candidate in candidates:
        left, right = stats.get(candidate[0]), stats.get(candidate[1])
        if not left or not right or left["distinct"] == 0:
            continue
        if right["rows"] == 0 or right["distinct"] != right["rows"] or right["filled"] != right["rows"]:
            continue
        kept.append(candidate)
    return kept


def rank(candidates, stats, background, conditioned=True):
    """Scores every candidate, in descending order of significance.

    Coverage is 1 for exact inclusions and is otherwise estimated from the sampled overlap, which
    avoids holding two supports in memory.
    """
    scored = []
    for dependent, referenced, degree in candidates:
        left, right = stats.get(dependent), stats.get(referenced)
        if not left or not right:
            continue
        if degree >= 1.0:
            coverage, sampled = 1.0, None
        else:
            coverage, sampled = background.overlap(dependent, referenced)
            if coverage is None:
                coverage = degree
        share = background.share(referenced, exclude=dependent,
                                 floor=right["filled"] / max(background.cells, 1),
                                 conditioned=conditioned)
        # A second null over distinct values, reported alongside the cell-weighted one.
        support = background.support_share(referenced, exclude=dependent,
                                           conditioned=conditioned)
        gcov, significance = inclusion_genuineness(coverage, left["distinct"], share)
        _, significance_support = inclusion_genuineness(coverage, left["distinct"], support)
        # Ranking uses the cell-weighted significance; the other two are recorded only, as they
        # did not rank better against the oracles.
        scored.append({"dependent": dependent, "referenced": referenced,
                       "distinct": left["distinct"], "referenced_distinct": right["distinct"],
                       "coverage": coverage, "sampled": sampled, "share": share,
                       "support_share": support,
                       "gcov": gcov, "significance": significance,
                       "significance_support": significance_support,
                       "significance_conservative": min(significance, significance_support)})
    scored.sort(key=lambda s: (-s["significance"], -s["gcov"], s["dependent"]))
    return scored


def read_oracle(export):
    """The declared foreign keys of a corpus, whichever name its oracle file carries."""
    for name in ("ground_truth.json", "unibench_oracle.json", "oracle.json"):
        path = export.parent / name
        if path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
            declared = document.get("dependencies") or document.get("expected_inds") or []
            return {(d["dependent"], d["referenced"]) for d in declared}
    return set()


def evaluate(scored, oracle, tested, alpha, label):
    """Precision and recall of the ranking against a declared foreign-key oracle."""
    total = len(scored)
    hits = sum(1 for s in scored if (s["dependent"], s["referenced"]) in oracle)
    print(f"{label}: {total:,} candidates scored, {hits} of {len(oracle)} declared keys among "
          f"them, {tested:,} tested by discovery\n")

    threshold = bonferroni(tested, alpha)
    cuts = [("all candidates", total),
            (f"significance > {threshold:.2f} (Bonferroni)",
             sum(1 for s in scored if s["significance"] > threshold)),
            ("largest score gap (HoPF cliff)", cliff([s["significance"] for s in scored]))]
    print(f"  {'cut':<38} {'kept':>9} {'precision':>10} {'recall':>16}")
    for name, keep in cuts:
        kept = scored[:keep]
        found = sum(1 for s in kept if (s["dependent"], s["referenced"]) in oracle)
        precision = found / len(kept) if kept else 0.0
        print(f"  {name:<38} {len(kept):>9,} {precision:>9.1%} "
              f"{found:>6}/{len(oracle)} {found / max(len(oracle), 1):>6.0%}")

    # Rank at which each share of the recoverable keys is reached.
    seen = 0
    marks = {}
    for position, entry in enumerate(scored, 1):
        if (entry["dependent"], entry["referenced"]) in oracle:
            seen += 1
            for target in (0.5, 0.75, 0.9, 1.0):
                if target not in marks and seen >= target * hits:
                    marks[target] = position
    print("\n  candidates a reader must go through, ranked by significance, to reach")
    for target, position in sorted(marks.items()):
        print(f"    {target:>5.0%} of the {hits} recoverable keys: {position:>9,}"
              f"   ({total / position:>6.1f}x of the list)")
    return threshold


def main():
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    export, results = Path(positional[0]), Path(positional[1])
    cap = int(flag("sample", 200_000))
    alpha = float(flag("alpha", 0.05))
    top = int(flag("top", 25))

    stats, background = scan_cached(export, cap, flag("cache"))
    print(f"background: {background.cells:,} non-empty cells, {len(background.count):,} values "
          f"sampled at 1 in {(1 << 32) // max(background.limit, 1):,}\n")

    candidates = read_inds(results)
    if "--prefilter" in sys.argv[1:]:
        before = len(candidates)
        candidates = prefilter(candidates, stats)
        print(f"prefilter: {before:,} -> {len(candidates):,} candidates\n")
    scored = rank(candidates, stats, background,
                  conditioned="--whole-instance" not in sys.argv[1:])

    # Unary discovery tests every ordered pair of distinct extractors.
    extractors = len(stats)
    oracle = read_oracle(export)
    if oracle:
        evaluate(scored, oracle, extractors * (extractors - 1), alpha, export.parent.name)

    print(f"\n  highest scoring {top}:")
    print(f"  {'sig':>10} {'gcov':>6} {'q':>10} {'|L|':>10}  candidate")
    for entry in scored[:top]:
        mark = "*" if (entry["dependent"], entry["referenced"]) in oracle else " "
        print(f"  {entry['significance']:>10.1f} {entry['gcov']:>6.3f} {entry['share']:>10.2e} "
              f"{entry['distinct']:>10,} {mark} {entry['dependent']} -> {entry['referenced']}")
    if oracle:
        print("  (* declared foreign key)")

    out = flag("json")
    if out:
        Path(out).write_text(json.dumps(scored, indent=2), encoding="utf-8")
        print(f"\n  written to {out}")


if __name__ == "__main__":
    main()
