"""The transformation language and transformation-aware IND discovery on tiny exports."""
import csv
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "lib"))
sys.path.insert(0, str(ROOT / "experiments" / "run"))

import transforms as tf
import discover_transforms


def write_export(directory, relations):
    """relations: {name: {column: [values]}} -> catalog.json plus one CSV per relation."""
    catalog = []
    for name, columns in relations.items():
        header = list(columns)
        rows = list(zip(*columns.values()))
        with (directory / f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        catalog.append({"relation": name, "scope": "base", "rows": len(rows),
                        "columns": len(header), "file": f"{name}.csv"})
    (directory / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")


class LanguageTest(unittest.TestCase):
    def test_spec_round_trip(self):
        for text in ["unpad|add_prefix:tt", "affine:100:0", "affine:1.8:32", "pad:7", "lower"]:
            self.assertEqual(tf.spec(tf.parse(text)), text)

    def test_string_steps(self):
        self.assertEqual(tf.apply(tf.parse("add_prefix:tt"), "0114709"), "tt0114709")
        self.assertEqual(tf.apply(tf.parse("strip_prefix:C-|add_prefix:c"), "C-007"), "c007")
        self.assertEqual(tf.apply(tf.parse("unpad"), "000"), "0")
        self.assertEqual(tf.apply(tf.parse("pad:5"), "42"), "00042")
        self.assertEqual(tf.apply(tf.parse("pad:5"), "x42"), "x42")  # padding only touches digits

    def test_affine_uses_the_target_precision(self):
        self.assertEqual(tf.apply(tf.parse("affine:1000:0"), "1.5", 0), "1500")
        self.assertEqual(tf.apply(tf.parse("affine:100:0"), "10.99", 0), "1099")
        self.assertEqual(tf.apply(tf.parse("affine:0.01:0"), "1099", 2), "10.99")
        self.assertIsNone(tf.apply(tf.parse("affine:100:0"), "n/a", 0))

    def test_numeric_target_is_canonical(self):
        self.assertEqual(tf.canonical_target(["1.50", "2.5"], True), {"1.50", "2.50"})


class DiscoveryTest(unittest.TestCase):
    def discover(self, relations, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            write_export(Path(tmp), relations)
            found, _, _ = discover_transforms.discover(Path(tmp), **kw)
        return {(f["dependent"], f["referenced"]): f["transform"] for f in found}

    def test_registry_prefix_is_inferred(self):
        ids = [f"{i * 37 + 11:07d}" for i in range(60)]
        found = self.discover({"links": {"imdbid": ids}, "films": {"imdb_id": ["tt" + v for v in ids]}})
        self.assertEqual(found[("links.imdbid", "films.imdb_id")], "add_prefix:tt")

    def test_unit_factor_on_sparse_values_is_inferred(self):
        # Irregular weights: in an arithmetic progression every value's neighbour is itself a value,
        # which the placebo rightly counts against the unit.
        rng = random.Random(7)
        kg = sorted({"%.3f" % rng.uniform(1, 90) for _ in range(40)})
        grams = [str(round(float(v) * 1000)) for v in kg]
        found = self.discover({"a": {"kg": kg}, "b": {"g": grams}})
        self.assertEqual(found[("a.kg", "b.g")], "affine:1000:0")

    def test_scaling_into_a_dense_key_range_is_rejected(self):
        # Keys 1..200 scaled by 1000 into a target that contains every integer up to 200,000: the
        # neighbours' images are covered just as well, so inclusion gives no evidence for the unit.
        keys = [str(i) for i in range(1, 201)]
        found = self.discover({"a": {"k": keys}, "b": {"dense": [str(i) for i in range(1, 200001)]}})
        self.assertNotIn(("a.k", "b.dense"), found)

    def test_equality_inclusions_need_no_transformation(self):
        ids = [str(i * 13) for i in range(50)]
        self.assertEqual(self.discover({"a": {"x": ids}, "b": {"y": ids}}), {})


if __name__ == "__main__":
    unittest.main()
