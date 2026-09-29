import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intermodel_pipeline as pipeline


class MaterializeMeasurementsTest(unittest.TestCase):
    def test_presence_and_coverage_use_the_same_capped_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left.csv"
            right = root / "right.csv"
            with left.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerows((("fk", "value"), ("1", "a"), ("2", "b"),
                                  ("3", "c"), ("4", "d"), ("5", "e")))
            with right.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerows((("id", "name"), ("1", "one"), ("2", "two"),
                                  ("3", "three"), ("4", "four"), ("5", "five")))

            sets = {
                "left": {"path": left, "columns": ["fk", "value"],
                         "filled": {"fk": 5}, "support": {"fk": {"1", "2", "3", "4", "5"}}},
                "right": {"path": right, "columns": ["id", "name"],
                          "filled": {"id": 5}, "support": {"id": {"1", "2", "3", "4", "5"}}},
            }
            inclusion = {"dependent_set": "left", "dependent": "fk",
                         "referenced_set": "right", "referenced": "id"}
            old_limit = pipeline.MAX_ROWS
            pipeline.MAX_ROWS = 2
            try:
                result = pipeline.materialize(inclusion, sets, root / "out")
            finally:
                pipeline.MAX_ROWS = old_limit

            self.assertEqual(2, result["matched"])
            self.assertEqual(1.0, result["presence"])
            self.assertEqual(1.0, result["coverage_bag"])
            self.assertEqual(result["participation"],
                             result["presence"] * result["coverage_bag"])


if __name__ == "__main__":
    unittest.main()
