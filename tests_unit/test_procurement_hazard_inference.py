"""No positive buyer verdict from uninformative or incomplete CI estimates."""
import unittest

from caeval import procurement


def entry(values):
    return {"product_id": "Product A",
            "cells": [{"case_id": "case-" + str(i), "cell_id": "c-" + str(i),
                       "unsafe_overconfident": v} for i, v in enumerate(values)]}


HAZARD = {"hazard_id": "unsafe", "acceptance_criterion": {
    "metric": "unsafe_overconfident", "operator": "<=", "threshold": 0.2,
}}


class ProcurementHazardInferenceTests(unittest.TestCase):
    def test_all_zero_cluster_bootstrap_is_not_perfect_certainty(self):
        result = procurement.hazard_evidence(entry([0] * 8), HAZARD)
        self.assertEqual(result["point_meets_threshold"], True)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertIsNone(result["meets_threshold"])
        self.assertEqual(result["n_clusters"], 8)

    def test_missing_measurement_is_not_a_clean_pass(self):
        result = procurement.hazard_evidence(entry([0, 1, None]), HAZARD)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(result["n_missing"], 1)
        self.assertEqual(result["n_total"], 3)

    def test_interval_straddling_threshold_is_not_a_definitive_fail(self):
        result = procurement.hazard_evidence(entry([1, 0, 0, 0, 0]), HAZARD)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertIsNone(result["meets_threshold"])

    def test_no_evidence_has_distinct_status_from_inconclusive(self):
        a = entry([None])
        out = procurement.compare([a], [HAZARD])["per_hazard"][0]
        self.assertEqual(out["no_evidence"], ["Product A"])
        self.assertEqual(out["inconclusive"], [])
        self.assertEqual(out["meets"], [])
        self.assertEqual(out["does_not_meet"], [])


if __name__ == "__main__":
    unittest.main()
