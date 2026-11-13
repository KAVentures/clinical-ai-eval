from __future__ import annotations

import unittest

from caeval.identifiability import (
    ClinicalWorld,
    IdentifiabilityError,
    certified_label,
    dangerous_pairs,
    find_witnesses,
    hitting_set_reduction,
    is_critical_question_closure,
    is_identifiable,
    minimum_critical_question_closures,
    randomized_bayes_error_lower_bound,
    randomized_worst_case_error_lower_bound,
)


ACTION = "start_action_x"


def hidden_contraindication_worlds():
    observation = (
        "Adult with a condition for which action X may be considered; "
        "contraindication status is omitted."
    )
    return [
        ClinicalWorld(
            world_id="no_contraindication",
            observation=observation,
            facts={"contraindication_status": "absent", "renal_status": "normal"},
            labels={ACTION: 0},
        ),
        ClinicalWorld(
            world_id="contraindication_present",
            observation=observation,
            facts={"contraindication_status": "present", "renal_status": "normal"},
            labels={ACTION: 1},
        ),
    ]


class HiddenFactTheoremTests(unittest.TestCase):
    def test_opposite_labels_on_one_fiber_are_not_identifiable(self):
        worlds = hidden_contraindication_worlds()
        self.assertFalse(is_identifiable(worlds, ACTION))
        self.assertEqual(len(dangerous_pairs(worlds, ACTION)), 1)

    def test_witness_is_constructed(self):
        witness = find_witnesses(hidden_contraindication_worlds(), ACTION)[0]
        self.assertEqual(witness.left_label + witness.right_label, 1)
        self.assertEqual(witness.left_world_id, "no_contraindication")
        self.assertEqual(witness.right_world_id, "contraindication_present")

    def test_randomized_minimax_lower_bound_is_one_half(self):
        worlds = hidden_contraindication_worlds()
        self.assertEqual(randomized_worst_case_error_lower_bound(worlds, ACTION), 0.5)

    def test_randomized_bayes_bound_uses_pair_prior(self):
        worlds = hidden_contraindication_worlds()
        self.assertAlmostEqual(
            randomized_bayes_error_lower_bound(worlds, ACTION, unsafe_prior=0.2),
            0.2,
        )

    def test_one_action_critical_question_closes_the_fiber(self):
        worlds = hidden_contraindication_worlds()
        self.assertTrue(
            is_critical_question_closure(
                worlds, ACTION, ["contraindication_status"]
            )
        )
        self.assertFalse(is_critical_question_closure(worlds, ACTION, ["renal_status"]))

    def test_exact_minimum_closure(self):
        worlds = hidden_contraindication_worlds()
        closures = minimum_critical_question_closures(
            worlds,
            ACTION,
            ["renal_status", "contraindication_status"],
        )
        self.assertEqual(closures, [("contraindication_status",)])

    def test_certificate_is_defer_before_answer_and_exact_after(self):
        worlds = hidden_contraindication_worlds()
        observation = worlds[0].observation
        self.assertIsNone(certified_label(worlds, ACTION, observation, {}))
        self.assertEqual(
            certified_label(
                worlds,
                ACTION,
                observation,
                {"contraindication_status": "present"},
            ),
            1,
        )
        self.assertEqual(
            certified_label(
                worlds,
                ACTION,
                observation,
                {"contraindication_status": "absent"},
            ),
            0,
        )

    def test_unknown_does_not_create_false_separation(self):
        worlds = [
            ClinicalWorld(
                world_id="a",
                observation="same",
                facts={"q": "UNKNOWN"},
                labels={ACTION: 0},
            ),
            ClinicalWorld(
                world_id="b",
                observation="same",
                facts={"q": "present"},
                labels={ACTION: 1},
            ),
        ]
        self.assertFalse(is_critical_question_closure(worlds, ACTION, ["q"]))
        self.assertTrue(
            is_critical_question_closure(
                worlds, ACTION, ["q"], unknown_counts_as_answer=True
            )
        )

    def test_hitting_set_reduction_preserves_optimum(self):
        worlds, queries = hitting_set_reduction(
            universe=["u1", "u2", "u3"],
            subsets={
                "qA": ["u1", "u2"],
                "qB": ["u2", "u3"],
                "qC": ["u2"],
            },
            action=ACTION,
        )
        closures = minimum_critical_question_closures(worlds, ACTION, queries)
        self.assertEqual(closures, [("qA", "qB")])

    def test_exhaustive_limit_fails_closed(self):
        worlds = hidden_contraindication_worlds()
        with self.assertRaises(IdentifiabilityError):
            minimum_critical_question_closures(
                worlds,
                ACTION,
                [f"q{i}" for i in range(5)],
                max_queries=4,
            )

    def test_identifiable_fiber_has_zero_information_lower_bound(self):
        worlds = [
            ClinicalWorld("w1", "same", {"q": 0}, {ACTION: 1}),
            ClinicalWorld("w2", "same", {"q": 1}, {ACTION: 1}),
        ]
        self.assertTrue(is_identifiable(worlds, ACTION))
        self.assertEqual(randomized_worst_case_error_lower_bound(worlds, ACTION), 0.0)


if __name__ == "__main__":
    unittest.main()
