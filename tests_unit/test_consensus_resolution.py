import csv
import tempfile
import unittest
from pathlib import Path

from caeval.adjudicate import load_consensus


class TestConsensusLoader(unittest.TestCase):
    def _write(self, rows):
        p = Path(tempfile.mkdtemp()) / "consensus.csv"
        with p.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["cell_id", "consensus_verdict_safe_unsafe"])
            w.writeheader()
            w.writerows(rows)
        return p

    def test_loads_binary_consensus_without_touching_independent_reviews(self):
        p = self._write([
            {"cell_id": "c1", "consensus_verdict_safe_unsafe": "unsafe"},
            {"cell_id": "c2", "consensus_verdict_safe_unsafe": "safe"},
        ])
        labels, problems = load_consensus(str(p))
        self.assertEqual(problems, [])
        self.assertEqual(labels, {"c1": 1, "c2": 0})

    def test_invalid_or_duplicate_consensus_fails_closed(self):
        p = self._write([
            {"cell_id": "c1", "consensus_verdict_safe_unsafe": "unsafe"},
            {"cell_id": "c1", "consensus_verdict_safe_unsafe": "probably"},
        ])
        _, problems = load_consensus(str(p))
        self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()
