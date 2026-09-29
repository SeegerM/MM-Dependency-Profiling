"""Discovers transformation-aware INDs: inclusions that hold once one representation is mapped
onto the other.

For example, MovieLens stores the IMDb identifier 0114709 where Wikidata stores tt0114709; under
`add_prefix:tt` the two are compared by equality. For each ordered pair of dimensions whose
equality coverage is below rho:

  1. Propose candidates from samples of both sides (experiments/lib/transforms.py): differing
     constant prefixes/suffixes, zero-padding or its removal, letter case, and for numeric pairs
     affine maps a*x + b from a catalogue of unit factors, compared at the target's precision.
  2. Screen on the sample at rho - 0.05, since sampled coverage varies around the true one.
  3. Require near-injectivity: at least 90% as many distinct images as sampled values.
  4. For numeric maps, require the candidate to beat a placebo (the same map applied to
     neighbouring source values) by `margin`, so a dense target range is not mistaken for a
     unit difference.
  5. Validate the best surviving candidate exactly on all values: coverage >= rho.

Only the simplest passing transformation is reported per pair (fewest steps, then highest
coverage).

usage: discover_transforms.py <exportDir> [--rho=0.9] [--sample=200] [--margin=0.5]
                              [--min-distinct=20] [--out=<report.txt>] [--json=<out.json>]
"""
import csv
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import transforms as tf  # noqa: E402

csv.field_size_limit(1 << 24)

# Unit factors a (and offsets b) tried for numeric pairs, each in both directions.
FACTORS = [10.0 ** k for k in range(1, 7)] + [60.0, 3600.0, 24.0, 2.54, 0.3048, 0.453592, 1.609344]
AFFINE = ([(a, 0.0) for a in FACTORS] + [(1.0 / a, 0.0) for a in FACTORS]
          + [(1.8, 32.0), (1.0 / 1.8, -32.0 / 1.8)])


def flag(name, default=None):
    prefix = "--" + name + "="
    return next((a[len(prefix):] for a in sys.argv[1:] if a.startswith(prefix)), default)


def load(export):
    catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    dims = {}
    for entry in catalog:
        with (export / entry["file"]).open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            seen = [set() for _ in header]
            for row in reader:
                for i, value in enumerate(row[:len(header)]):
                    if value != "":
                        seen[i].add(value)
        for i, column in enumerate(header):
            if seen[i]:
                dims[(entry["relation"], column)] = seen[i]
    return dims


def common_prefix(values):
    values = list(values)
    if not values:
        return ""
    first, last = min(values), max(values)
    i = 0
    while i < min(len(first), len(last)) and first[i] == last[i]:
        i += 1
    return first[:i]


def constant_part(values, side):
    """The non-digit constant a sample shares at its start or end (a registry tag, a unit)."""
    if side == "prefix":
        p = common_prefix(values)
        return p[:len(p) - len(p.lstrip("".join(c for c in p if not c.isdigit())))] if p else ""
    p = common_prefix(v[::-1] for v in values)[::-1]
    j = len(p)
    while j > 0 and not p[j - 1].isdigit():
        j -= 1
    return p[j:] if p else ""


def shape(values):
    sample = list(values)
    numeric = sum(tf.to_number(v) is not None for v in sample) >= 0.9 * len(sample)
    digits = [v for v in sample if v.isdigit()]
    return {
        "numeric": numeric,
        "prefix": constant_part(sample, "prefix"),
        "suffix": constant_part(sample, "suffix"),
        "width": Counter(len(v) for v in digits).most_common(1)[0][0] if digits else None,
        "fixed_width": bool(digits) and len({len(v) for v in digits}) == 1,
        "zeros": any(v.startswith("0") and len(v) > 1 for v in digits),
        "letters": any(c.isalpha() for v in sample for c in v),
        "upper": all(v == v.upper() for v in sample) and any(c.isalpha() for v in sample for c in v),
        "capitalized": all(v == v.capitalize() for v in sample) and any(c.isalpha() for v in sample for c in v),
        "lower": all(v == v.lower() for v in sample) and any(c.isalpha() for v in sample for c in v),
        "decimals": tf.decimals_of(sample) if numeric else 0,
    }


def candidates(sd, st):
    """Transformations the two samples' shapes suggest."""
    out = set()
    string_steps = []
    if sd["prefix"] != st["prefix"]:
        step = []
        if sd["prefix"]:
            step.append(("strip_prefix", sd["prefix"]))
        if st["prefix"]:
            step.append(("add_prefix", st["prefix"]))
        string_steps.append(tuple(step))
    if sd["suffix"] != st["suffix"]:
        step = []
        if sd["suffix"]:
            step.append(("strip_suffix", sd["suffix"]))
        if st["suffix"]:
            step.append(("add_suffix", st["suffix"]))
        string_steps.append(tuple(step))
    if st["fixed_width"] and st["zeros"] and st["width"]:
        string_steps.append((("pad", st["width"]),))
    if sd["zeros"] and not st["zeros"]:
        string_steps.append((("unpad",),))
    if sd["letters"] and st["upper"] and not sd["upper"]:
        string_steps.append((("upper",),))
    if sd["letters"] and st["lower"] and not sd["lower"]:
        string_steps.append((("lower",),))
    if sd["letters"] and st["capitalized"] and not sd["capitalized"]:
        string_steps.append((("capitalize",),))
    for s in string_steps:
        out.add(s)
    # Two string steps together: a prefix change after padding or unpadding, a case change with a
    # prefix -- the combinations registry identifiers actually show.
    for a in string_steps:
        for b in string_steps:
            if a != b:
                out.add(a + b)
    if sd["numeric"] and st["numeric"]:
        for a, b in AFFINE:
            out.add((("affine", a, b),))
    return {c for c in out if c}


def granularity(target, decimals):
    """The most common gap between consecutive sorted numeric target values (at least one unit)."""
    unit = tf.Decimal(1).scaleb(-decimals)
    numbers = sorted({tf.Decimal(v) for v in target})
    gaps = Counter(b - a for a, b in zip(numbers, numbers[1:]))
    return max(gaps.most_common(1)[0][0], unit) if gaps else unit


def neighbour(value, step):
    """A numeric source value one step of the dependent's granularity away."""
    x = tf.to_number(value)
    if x is None or step is None:
        return value
    return format(x + step, "f")


def coverage(transform, values, target, decimals):
    hit = 0
    for v in values:
        image = tf.apply(transform, v, decimals)
        if image is not None and image in target:
            hit += 1
    return hit / len(values) if values else 0.0


def discover(export, rho=0.9, sample_size=200, margin=0.5, min_distinct=20, seed=20260928, slack=0.05):
    start = time.perf_counter()
    dims = load(export)
    rng = random.Random(seed)
    samples = {d: rng.sample(sorted(v), min(sample_size, len(v))) for d, v in dims.items()}
    shapes = {d: shape(samples[d]) for d in dims}
    canon = {}
    granularity_cache = {}
    found = []
    for dep, dep_values in dims.items():
        if len(dep_values) < min_distinct:
            continue
        sample = samples[dep]
        for tgt, tgt_values in dims.items():
            if tgt == dep or len(tgt_values) < min_distinct:
                continue
            sd, st = shapes[dep], shapes[tgt]
            # Screening on the sample; the equality test on all values comes last.
            eq = sum(v in tgt_values for v in sample) / len(sample)
            screen = rho - slack  # sampled coverage varies; the exact check below uses rho
            if eq >= rho:
                continue
            best = None
            for transform in candidates(sd, st):
                numeric = tf.is_numeric_transform(transform)
                if numeric not in canon.setdefault(tgt, {}):
                    canon[tgt][numeric] = tf.canonical_target(tgt_values, numeric)
                target = canon[tgt][numeric]
                decimals = st["decimals"] if numeric else None
                cov = coverage(transform, sample, target, decimals)
                if cov < screen:
                    continue
                images = [tf.apply(transform, v, decimals) for v in sample]
                if len({i for i in images if i is not None}) < 0.9 * len(sample):
                    continue  # not near-injective: the mapping collapses values
                # Placebo, for numeric maps only: a catalogue factor is a free parameter, so it must
                # explain the dependent's values and not their neighbours one granularity step away.
                # String maps are read off a visible difference and need no placebo.
                shifted = 0.0
                if numeric:
                    if dep not in granularity_cache:
                        numbers = [v for v in sample if tf.to_number(v) is not None]
                        granularity_cache[dep] = granularity(numbers, tf.decimals_of(numbers))
                    shifted = sum(tf.apply(transform, neighbour(v, granularity_cache[dep]),
                                           decimals) in target
                                  for v in sample) / len(sample)
                    if cov - shifted < margin:
                        continue
                key = (len(transform), -cov)
                if best is None or key < best[0]:
                    best = (key, transform, cov, shifted, decimals)
            if best is None:
                continue
            _, transform, cov_s, shifted, decimals = best
            full = coverage(transform, sorted(dep_values), canon[tgt][tf.is_numeric_transform(transform)],
                            decimals)
            if full < rho:
                continue
            equality = len(dep_values & tgt_values) / len(dep_values)
            found.append({"dependent": ".".join(dep), "referenced": ".".join(tgt),
                          "transform": tf.spec(transform), "coverage": full, "equality": equality,
                          "placebo": shifted, "distinct": len(dep_values)})
    return found, len(dims), time.perf_counter() - start


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
        print(__doc__.strip().splitlines()[-2])
        return 2
    export = Path(sys.argv[1])
    rho = float(flag("rho", "0.9"))
    found, n, seconds = discover(export, rho, int(flag("sample", "200")), float(flag("margin", "0.5")),
                                 int(flag("min-distinct", "20")))
    lines = ["# Transformation-aware INDs (experiments/run/discover_transforms.py). No oracle is read.",
             "", "export      %s" % export, "rho         %g" % rho, "dimensions  %d" % n,
             "found       %d" % len(found), "time        %.1f s" % seconds, ""]
    for f in sorted(found, key=lambda f: (f["dependent"], f["referenced"])):
        lines.append("  %s -> %s  [%s]  coverage %.4f (equality %.4f, placebo %.2f)" % (
            f["dependent"], f["referenced"], f["transform"], f["coverage"], f["equality"], f["placebo"]))
    text = "\n".join(lines) + "\n"
    print(text)
    if flag("out"):
        Path(flag("out")).write_text(text, encoding="utf-8")
    if flag("json"):
        Path(flag("json")).write_text(json.dumps(found, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
