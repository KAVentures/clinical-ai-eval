"""No-network tests for the previously published evidence provenance audit."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_prior_evidence import (
    agreement,
    audit_readiness,
    audit_sufficiency,
    kappa,
    read_labels,
)


class RetrospectiveEvidenceAuditTests(unittest.TestCase):
    def test_kappa_and_alignment(self):
        self.assertEqual(kappa([0, 1, 0, 1], [0, 1, 0, 1]), 1.0)
        self.assertEqual(kappa([0, 1, 0, 1], [1, 0, 1, 0]), -1.0)
        self.assertIsNone(kappa([1, 1], [1, 1]))
        with self.assertRaises(ValueError):
            agreement({"x": 1}, {"y": 1})

    def test_reject_duplicate_and_unknown_labels(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "labels.csv"
            p.write_text("annotation_id,human_label\nA001,0\nA001,1\n")
            with self.assertRaises(ValueError):
                read_labels(p)
            p.write_text("annotation_id,human_label\nA001,maybe\n")
            with self.assertRaises(ValueError):
                read_labels(p)

    def make_readiness_fixture(self, root):
        human = root / "runs/human_eval"
        votes_dir = root / "runs/judge_panel"
        human.mkdir(parents=True)
        votes_dir.mkdir(parents=True)
        ids = [f"A{i:03d}" for i in range(1, 51)]
        labels = {
            "human_labels.csv": {id_: (i % 2) for i, id_ in enumerate(ids)},
            "labels_O.csv": {id_: (i % 2) for i, id_ in enumerate(ids)},
            "labels_G.csv": {id_: (i % 3 == 0) * 1 for i, id_ in enumerate(ids)},
        }
        for filename, mapping in labels.items():
            with (human / filename).open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["annotation_id", "human_label"])
                for item in ids:
                    writer.writerow([item, mapping[item]])

        key = {}
        for i, item in enumerate(ids):
            key[item] = {
                "subject_model": f"model-{i % 4}",
                "item": i,
                "prompt_id": f"prompt-{i}",
            }
        (human / "key.json").write_text(json.dumps(key))
        with (votes_dir / "votes.jsonl").open("w") as f:
            for model in range(4):
                for item in range(50):
                    entry = {
                        "subject_model": f"model-{model}",
                        "item": item,
                        "prompt_id": f"prompt-{item}",
                        "votes": {name: bool(item % 2) for name in ["openai", "anthropic", "xai", "google"]},
                    }
                    f.write(json.dumps(entry) + "\n")
        report = {
            "n_common": 50,
            "per_annotator": {
                "R1": {"rate": sum(labels["human_labels.csv"].values()) / 50},
                "O": {"rate": sum(labels["labels_O.csv"].values()) / 50},
                "G": {"rate": sum(labels["labels_G.csv"].values()) / 50},
            },
        }
        (human / "human_validity_multi.json").write_text(json.dumps(report))
        return human

    def test_readiness_fixture_and_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            human = self.make_readiness_fixture(root)
            output = audit_readiness(root)
            self.assertEqual(output["n_human_items"], 50)
            self.assertEqual(output["n_provider_judge_votes"], 800)
            (human / "labels_O.csv").write_text((human / "labels_O.csv").read_text().replace("A001,0", "A001,2"))
            with self.assertRaises(ValueError):
                audit_readiness(root)

    def make_sufficiency_fixture(self, root):
        target = root / "outputs/tables"
        target.mkdir(parents=True)
        with (target / "panel_table3_primary_outcome.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "prompt_condition", "unsafe_overconfident_n", "total_n", "rate",
                "absolute_risk_difference_standard_minus_wrapper", "n_paired_cells",
            ])
            writer.writeheader()
            for name, unsafe in (("standard", 592), ("evidence_sufficiency", 296)):
                writer.writerow({
                    "prompt_condition": name, "unsafe_overconfident_n": unsafe,
                    "total_n": 1200, "rate": unsafe / 1200,
                    "absolute_risk_difference_standard_minus_wrapper": 296 / 1200,
                    "n_paired_cells": 1200,
                })
        (target / "panel_analysis_summary.json").write_text(json.dumps({
            "primary_common_panel": {"risk_difference": 296 / 1200}
        }))
        return target

    def test_sufficiency_consistency_and_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = self.make_sufficiency_fixture(root)
            report = audit_sufficiency(root)
            self.assertAlmostEqual(report["previously_published_absolute_difference"], 296 / 1200)
            p = target / "panel_analysis_summary.json"
            p.write_text('{"primary_common_panel":{"risk_difference":0.1}}')
            with self.assertRaises(ValueError):
                audit_sufficiency(root)


if __name__ == "__main__":
    unittest.main()
