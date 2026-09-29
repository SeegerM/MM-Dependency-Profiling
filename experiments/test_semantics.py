import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "lib"))
sys.path.insert(0, str(ROOT / "profile"))

import dependency_key
import result_kind


class DependencyIdentityTest(unittest.TestCase):
    def test_graph_relationship_types_do_not_collapse_to_endpoint(self):
        lan = dependency_key.key("neo4j:-[coms_lan]->.source.id", "coms.user.id")
        wlan = dependency_key.key("neo4j:-[coms_wlan]->.source.id", "coms.user.id")
        self.assertNotEqual(lan, wlan)

    def test_composite_reordering_is_the_same_mapping(self):
        first = dependency_key.key("hpatent.class.(cat,subcat)",
                                   "hpatent.category.(cat,subcat)")
        reordered = dependency_key.key("hpatent.class.(subcat,cat)",
                                       "hpatent.category.(subcat,cat)")
        self.assertEqual(first, reordered)

    def test_arity_mismatch_cannot_partially_match(self):
        unary = dependency_key.key("left.a", "right.b")
        mismatch = dependency_key.key("left.(a,c)", "right.b")
        self.assertNotEqual(unary, mismatch)


class ResultKindTest(unittest.TestCase):
    def test_semantic_near_key_is_separate_from_display_floor(self):
        self.assertTrue(result_kind.near_key(10, 10, 10))
        self.assertFalse(result_kind.qualifies_as_reference_target(10, 10, 10))
        self.assertTrue(result_kind.qualifies_as_reference_target(25, 25, 25))

    def test_shared_domain_is_not_reported_as_reference(self):
        kind, evidence = result_kind.classify(
            dependent_distinct=100, referenced_distinct=100, overlap=100,
            coverage=1.0, referenced_is_key=False, reverse_holds=True,
            domain_size=10_000)
        self.assertEqual("shared domain", kind)
        self.assertEqual(1.0, evidence["cardinality_ratio"])


if __name__ == "__main__":
    unittest.main()
