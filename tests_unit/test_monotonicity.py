from __future__ import annotations

import unittest

from caeval.monotonicity import (
    evaluate_declared_action_sets,
    resolved_safe_sets_from_branch_risks,
    summarize_evaluations,
    unresolved_robust_safe_set_from_branch_risks,
    verify_branch_intersection_identity,
)


class BranchIntersectionTheoremTests(unittest.TestCase):
    def setUp(self):
        # Abstract actions: no clinical correctness is encoded in this software fixture.
        self.risks = {
            "resolution_alpha": {"A": 0.2, "B": 0.4, "C": 1.3},
            "resolution_beta": {"A": 0.3, "B": 1.1, "C": 0.5},
        }

    def test_unresolved_safe_set_is_branch_intersection(self):
        resolved = resolved_safe_sets_from_branch_risks(self.risks, risk_budget=1.0)
        self.assertEqual(resolved["resolution_alpha"], frozenset({"A", "B"}))
        self.assertEqual(resolved["resolution_beta"], frozenset({"A", "C"}))
        self.assertEqual(
            unresolved_robust_safe_set_from_branch_risks(self.risks, 1.0),
            frozenset({"A"}),
        )
        self.assertTrue(verify_branch_intersection_identity(self.risks, 1.0))

    def test_branch_menus_must_match(self):
        malformed = {
            "one": {"A": 0.1, "B": 0.2},
            "two": {"A": 0.1},
        }
        with self.assertRaisesRegex(ValueError, "same candidate-action menu"):
            resolved_safe_sets_from_branch_risks(malformed, 1.0)


class DeclaredActionConsistencyTests(unittest.TestCase):
    def test_detects_action_accepted_unresolved_but_rejected_in_plausible_branch(self):
        result = evaluate_declared_action_sets(
            case_id="fixture-1",
            candidate_actions=["A", "B", "C"],
            unresolved_autonomous_actions=["A", "B"],
            resolved_branch_actions={
                "resolution_alpha": ["A", "B"],
                "resolution_beta": ["A", "C"],
            },
        )
        self.assertFalse(result.consistent)
        self.assertEqual(result.branch_intersection, frozenset({"A"}))
        self.assertEqual(result.violating_actions, frozenset({"B"}))
        self.assertEqual(result.conservative_contraction, frozenset())

    def test_conservative_contraction_is_not_a_safety_violation(self):
        result = evaluate_declared_action_sets(
            case_id="fixture-2",
            candidate_actions=["A", "B", "C"],
            unresolved_autonomous_actions=[],
            resolved_branch_actions={
                "resolution_alpha": ["A", "B"],
                "resolution_beta": ["A", "C"],
            },
        )
        self.assertTrue(result.consistent)
        self.assertEqual(result.violating_actions, frozenset())
        self.assertEqual(result.conservative_contraction, frozenset({"A"}))

    def test_unknown_action_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown candidate action"):
            evaluate_declared_action_sets(
                case_id="fixture-3",
                candidate_actions=["A", "B"],
                unresolved_autonomous_actions=["Z"],
                resolved_branch_actions={"one": ["A"], "two": ["B"]},
            )

    def test_requires_multiple_resolved_branches(self):
        with self.assertRaisesRegex(ValueError, "at least two"):
            evaluate_declared_action_sets(
                case_id="fixture-4",
                candidate_actions=["A"],
                unresolved_autonomous_actions=[],
                resolved_branch_actions={"only": ["A"]},
            )

    def test_aggregate_keeps_violation_and_conservatism_separate(self):
        bad = evaluate_declared_action_sets(
            case_id="bad",
            candidate_actions=["A", "B", "C"],
            unresolved_autonomous_actions=["A", "B"],
            resolved_branch_actions={"one": ["A", "B"], "two": ["A", "C"]},
        )
        cautious = evaluate_declared_action_sets(
            case_id="cautious",
            candidate_actions=["A", "B"],
            unresolved_autonomous_actions=[],
            resolved_branch_actions={"one": ["A"], "two": ["A", "B"]},
        )
        summary = summarize_evaluations([bad, cautious])
        self.assertEqual(summary["n_cases"], 2)
        self.assertEqual(summary["n_candidate_action_opportunities"], 5)
        self.assertEqual(summary["n_violating_actions"], 1)
        self.assertEqual(summary["case_violation_rate"], 0.5)
        self.assertEqual(summary["candidate_action_violation_rate"], 0.2)
        self.assertEqual(summary["unresolved_autonomy_rate"], 0.4)
        self.assertEqual(summary["conservative_contraction_rate"], 0.2)


if __name__ == "__main__":
    unittest.main()
