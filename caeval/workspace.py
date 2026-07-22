"""A run WORKSPACE on disk — the unit the CLI stages hand off through, so
generation, judging, reporting, and adjudication are separable (§10). Judging a
different panel over the SAME frozen subject responses is the cost-saving path the
evidence-sufficiency study relied on (re-judge without re-generating).

Layout:
  <workspace>/
    run_meta.json        subject spec, family, panel, seeds, inference settings
    responses.jsonl      frozen subject responses (one row per cell) — the judge input
    results.jsonl        scored cells (written by the score/report stage)
    final_report.md, limitations.md, provenance.json, human_review.csv
    adjudication/        filled human_review.csv + adjudication_report.json (L2)
"""
from __future__ import annotations

import json
from pathlib import Path


class Workspace:
    def __init__(self, path):
        self.path = Path(path)

    def ensure(self):
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "adjudication").mkdir(exist_ok=True)
        return self

    # ---- paths ----
    @property
    def run_meta(self): return self.path / "run_meta.json"
    @property
    def responses(self): return self.path / "responses.jsonl"
    @property
    def results(self): return self.path / "results.jsonl"
    @property
    def human_review(self): return self.path / "human_review.csv"
    @property
    def final_report(self): return self.path / "final_report.md"
    @property
    def adjudication_dir(self): return self.path / "adjudication"

    # ---- io ----
    def write_run_meta(self, meta: dict):
        self.ensure()
        self.run_meta.write_text(json.dumps(meta, indent=2))

    def read_run_meta(self) -> dict:
        return json.loads(self.run_meta.read_text())

    def write_responses(self, rows: list[dict]):
        self.ensure()
        with open(self.responses, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def read_responses(self) -> list[dict]:
        return [json.loads(l) for l in open(self.responses) if l.strip()]

    def exists(self) -> bool:
        return self.run_meta.exists() and self.responses.exists()
