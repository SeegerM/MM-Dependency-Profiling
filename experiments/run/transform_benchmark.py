"""Injects representation differences into TPC-H and checks that the transformation is recovered.

Two kinds of injection, each producing an export with one known transformed inclusion:

  reference   one of TPC-H's eight seeded foreign keys has its dependent column rewritten by a
              transformation (experiments/datasets/perturb_export.py --transform): a registry
              prefix, a suffix, zero-padding, a unit scale (x1000) or a conversion (/100). The
              inclusion then holds only under the inverse transformation.
  attribute   a second store receives a copy of one attribute in another representation: letter
              case, cents (x100), degrees Fahrenheit (1.8x + 32), or grams with sub-precision noise
              (x1000 plus a jitter below the source's last digit, so only the gram-to-kilogram
              direction, rounded to the coarser precision, holds).

A case is recovered when discover_transforms.py reports the injected pair (or its converse) with a
transformation that maps the injected values back onto the originals. Every other reported
inclusion is spurious; the clean export is run first and its findings are the baseline.

usage: transform_benchmark.py [--work=$WORK_ROOT] [--out=paper/tables/transform-benchmark.txt]
"""
import csv
import json
import random
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "experiments" / "lib"))
sys.path.insert(0, str(HERE / "experiments" / "run"))
sys.path.insert(0, str(HERE / "experiments" / "datasets"))
import paths  # noqa: E402
import transforms as tf  # noqa: E402
from discover_transforms import discover  # noqa: E402
from perturb_export import transform_column  # noqa: E402

REFERENCE_TRANSFORMS = ["add_prefix:C-", "add_suffix:-X", "pad:12", "affine:1000:0", "affine:0.01:0"]
ATTRIBUTE_COPIES = [
    # (label, source relation, source column, injected transformation, decimals of the copy, jitter)
    ("case", "neo4j:Supplier", "s_name", "lower", None, 0),
    ("cents", "neo4j:Part", "p_retailprice", "affine:100:0", 0, 0),
    ("fahrenheit", "public.customer", "c_acctbal", "affine:1.8:32", 2, 0),
    ("grams, finer precision", "neo4j:Part", "p_retailprice", "affine:1000:0", 0, 4),
]


def flag(name, default=None):
    prefix = f"--{name}="
    return next((a[len(prefix):] for a in sys.argv[1:] if a.startswith(prefix)), default)


def split(name):
    """(relation, column) of a dependency side, for the relation names the TPC-H export uses."""
    for rel in ("shop.orders@lineitems", "neo4j:-[SUPPLIES]->", "neo4j:Supplier", "neo4j:Part",
                "public.customer", "public.nation", "public.region", "shop.orders"):
        if name.startswith(rel + "."):
            return rel, name[len(rel) + 1:]
    raise ValueError(name)


def column_values(export, relation, column):
    catalog = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    entry = next(e for e in catalog if e["relation"] == relation)
    with (export / entry["file"]).open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        i = next(reader).index(column)
        return [r[i] for r in reader if i < len(r) and r[i] != ""]


def add_copy(source, target, label, relation, column, transform, decimals, jitter, seed=7):
    """The export plus a scope `copy.<label>` holding the transformed distinct values."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    rng = random.Random(seed)
    values = sorted(set(column_values(source, relation, column)))
    out = []
    for v in values:
        image = tf.apply(tf.parse(transform), v, decimals if decimals is not None else None)
        if jitter and image is not None:
            image = str(int(image) + rng.randint(-jitter, jitter))
        out.append(image)
    name = "copy." + label.split(",")[0].replace(" ", "_")
    file = name + ".csv"
    with (target / file).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["value"])
        writer.writerows([[v] for v in out])
    catalog = json.loads((target / "catalog.json").read_text(encoding="utf-8"))
    catalog.append({"relation": name, "store": "document", "scope": "base", "rows": len(out),
                    "columns": 1, "file": file})
    (target / "catalog.json").write_text(json.dumps(catalog, indent=1), encoding="utf-8")
    return name + ".value", dict(zip(out, values))


def recovered(found, dependent, referenced, back):
    """Whether a finding states the injected pair, in either direction, with a correct inverse.

    `back` maps each injected value to its original. The found transformation is correct when it
    maps the injected values onto their originals (dependent side) or the originals onto the
    injected values (converse).
    """
    sample = list(back.items())[:500]
    for f in found:
        t = tf.parse(f["transform"])
        if (f["dependent"], f["referenced"]) == (dependent, referenced):
            ok = sum(eq(tf.apply(t, a, decimals_for(t, b)), b) for a, b in sample)
        elif (f["dependent"], f["referenced"]) == (referenced, dependent):
            ok = sum(eq(tf.apply(t, b, decimals_for(t, a)), a) for a, b in sample)
        else:
            continue
        if ok >= 0.95 * len(sample):
            return f
    return None


def decimals_for(transform, example):
    return tf.decimals_of([example]) if tf.is_numeric_transform(transform) else None


def eq(image, expected):
    if image is None:
        return False
    x, y = tf.to_number(image), tf.to_number(expected)
    return image == expected or (x is not None and y is not None and x == y)


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    out = Path(flag("out", str(HERE / "paper" / "tables" / "transform-benchmark.txt")))
    source = work / "mm_tpch" / "metalevel"
    scratch = work / "transform-bench"
    oracle = json.loads((work / "mm_tpch" / "ground_truth.json").read_text(encoding="utf-8"))
    fks = [(e["dependent"], e["referenced"]) for e in oracle["dependencies"] if "dependent" in e]
    fks = list(dict.fromkeys(fks))

    baseline, _, seconds = discover(source)
    base_pairs = {(f["dependent"], f["referenced"]) for f in baseline}
    lines = ["# Transformation-aware INDs on TPC-H with injected representation differences.",
             "# Generated by experiments/run/transform_benchmark.py; ground truth by construction.",
             "", "clean export: %d transformed inclusions (%.1f s)" % (len(baseline), seconds), ""]
    for f in baseline:
        lines.append("  baseline: %s -> %s [%s] %.4f" % (f["dependent"], f["referenced"], f["transform"], f["coverage"]))

    lines += ["## reference injections (dependent column rewritten)",
              "  %-46s %-15s %-10s %-26s %8s %9s" % ("reference", "injected", "recovered", "found transformation",
                                                       "coverage", "spurious")]
    totals = {"cases": 0, "recovered": 0, "spurious": 0}
    for dependent, referenced in fks:
        relation, column = split(dependent)
        originals = column_values(source, relation, column)
        for spec_text in REFERENCE_TRANSFORMS:
            transform = tf.parse(spec_text)
            decimals = 2 if spec_text == "affine:0.01:0" else 0 if tf.is_numeric_transform(transform) else None
            export = scratch / "ref"
            transform_column(source, export, transform, relation, column, decimals)
            back = {tf.apply(transform, v, decimals): v for v in set(originals)}
            found, _, _ = discover(export)
            hit = recovered(found, dependent, referenced, back)
            spurious = [f for f in found if f is not hit and (f["dependent"], f["referenced"]) not in base_pairs
                        and dependent not in (f["dependent"], f["referenced"])]
            totals["cases"] += 1
            totals["recovered"] += hit is not None
            totals["spurious"] += len(spurious)
            lines.append("  %-46s %-15s %-10s %-26s %8s %9d" % (
                dependent[:46], spec_text, "yes" if hit else "NO", hit["transform"] if hit else "-",
                "%.4f" % hit["coverage"] if hit else "-", len(spurious)))
            for f in spurious:
                lines.append("      spurious: %s -> %s [%s]" % (f["dependent"], f["referenced"], f["transform"]))

    lines += ["", "## attribute copies in a second store",
              "  %-24s %-26s %-16s %-10s %-26s %8s %9s" % ("copy", "of", "injected", "recovered",
                                                            "found transformation", "coverage", "spurious")]
    for label, relation, column, spec_text, decimals, jitter in ATTRIBUTE_COPIES:
        export = scratch / "copy"
        name, back = add_copy(source, export, label, relation, column, spec_text, decimals, jitter)
        found, _, _ = discover(export)
        hit = recovered(found, name, relation + "." + column, back)
        spurious = [f for f in found if f is not hit and (f["dependent"], f["referenced"]) not in base_pairs
                    and name not in (f["dependent"], f["referenced"])]
        totals["cases"] += 1
        totals["recovered"] += hit is not None
        totals["spurious"] += len(spurious)
        lines.append("  %-24s %-26s %-16s %-10s %-26s %8s %9d" % (
            label, (relation + "." + column)[:26], spec_text + (" +-%d" % jitter if jitter else ""),
            "yes" if hit else "NO", hit["transform"] if hit else "-",
            "%.4f" % hit["coverage"] if hit else "-", len(spurious)))
        for f in spurious:
            lines.append("      spurious: %s -> %s [%s]" % (f["dependent"], f["referenced"], f["transform"]))
    shutil.rmtree(scratch, ignore_errors=True)
    lines += ["", "  %d/%d injected transformations recovered; %d spurious transformed inclusions."
              % (totals["recovered"], totals["cases"], totals["spurious"])]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
