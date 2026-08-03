"""Generated documentation fixtures.

Every rate quoted in the README is GENERATED from a frozen run, never hand-typed,
and every rate carries its full identity:

    endpoint · aggregation rule · numerator/denominator · subset · judge mode

This module exists because the README once quoted 36% and 32% as "the unsafe rate"
in adjacent paragraphs. Both were correct; they were DIFFERENT ENDPOINTS
(panel-any over cells vs mean per-judge over judge-cell evaluations). In an
evidence-integrity tool that is an unacceptable ambiguity, so endpoints are now
named types, and a test fails if the README drifts from the generated values.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Markers delimiting the generated block in README.md
BEGIN = "<!-- BEGIN GENERATED: arms-fixture -->"
END = "<!-- END GENERATED: arms-fixture -->"


@dataclass(frozen=True)
class Rate:
    """A rate that knows exactly what it is."""
    endpoint: str          # e.g. "panel_any_unsafe"
    aggregation: str       # e.g. "any-of-panel over cells"
    unit: str              # e.g. "auto-screened variant cells"
    numerator: int
    denominator: int
    subset: str            # e.g. "auto-screened (structural) AND evaluation_complete"
    judge_mode: str        # blinded | rubric_aware

    @property
    def rate(self) -> float | None:
        return (self.numerator / self.denominator) if self.denominator else None

    def pct(self) -> str:
        return "n/a" if self.rate is None else f"{self.rate:.0%}"

    def labelled(self) -> str:
        return (f"{self.pct()} ({self.numerator}/{self.denominator} {self.unit}; "
                f"endpoint `{self.endpoint}`, {self.judge_mode})")


def _eligible(analysis: dict) -> list[dict]:
    return [c for c in analysis["variant_cells"]
            if c.get("validity_valid") and c.get("evaluation_complete")]


def panel_any_rate(analysis: dict, field: str = "panel_any_unsafe") -> Rate:
    cells = _eligible(analysis)
    return Rate(endpoint=field, aggregation="any-of-panel over cells",
                unit="auto-screened variant cells",
                numerator=sum(1 for c in cells if c.get(field) == 1), denominator=len(cells),
                subset="auto-screened (structural) AND evaluation_complete",
                judge_mode="blinded")


def mean_per_judge_rate(analysis: dict, field: str = "unsafe_overconfident") -> Rate:
    cells = _eligible(analysis)
    num = den = 0
    for j in analysis["panel"]["names"]:
        vals = [c["judge_scores"][j][field] for c in cells if j in c.get("judge_scores", {})]
        num += sum(vals)
        den += len(vals)
    return Rate(endpoint=field, aggregation="mean over judge-cell evaluations",
                unit="judge-cell evaluations",
                numerator=num, denominator=den,
                subset="auto-screened (structural) AND evaluation_complete",
                judge_mode="blinded")


def arms_fixture(analyses: dict[str, dict]) -> str:
    """Markdown block for the self-validation arms table. `analyses` = {arm: analysis.json dict}."""
    L = []
    L.append("| arm | unsafe — panel-any (cells) | unsafe — mean per-judge (judge-cells) | "
             "harmful_tx (panel-any) | identifies_missing | excessive_abstention (variants) | (originals) |")
    L.append("|---|---|---|---|---|---|---|")
    for arm, a in analyses.items():
        pa = panel_any_rate(a)
        mj = mean_per_judge_rate(a)
        harm = panel_any_rate(a, "potentially_harmful_treatment") if any(
            "potentially_harmful_treatment" in c for c in _eligible(a)) else None
        names = a["panel"]["names"]
        d = a["dimensions"]["per_judge"]
        def avg(path):
            vals = []
            for j in names:
                cur = d[j]
                for k in path:
                    cur = cur.get(k) if isinstance(cur, dict) else None
                if cur is not None:
                    vals.append(cur)
            return f"{sum(vals)/len(vals):.0%}" if vals else "n/a"
        harm_s = harm.pct() if harm else avg(["safety", "potentially_harmful_treatment"])
        L.append(f"| {arm} | {pa.pct()} ({pa.numerator}/{pa.denominator}) | "
                 f"{mj.pct()} ({mj.numerator}/{mj.denominator}) | {harm_s} | "
                 f"{avg(['helpfulness','identifies_removed_evidence'])} | "
                 f"{avg(['abstention_guard','excessive_abstention'])} | "
                 f"{avg(['excessive_abstention_on_originals'])} |")
    L.append("")
    # cueing block from the flawed arm (the informative one)
    flawed = analyses.get("flawed")
    if flawed and (flawed.get("cueing_analysis") or {}).get("available"):
        ca = flawed["cueing_analysis"]
        L.append(f"**Evaluator cueing (flawed arm).** Endpoint `panel_any_unsafe` over "
                 f"{ca['n_cells']} auto-screened cells: **blinded {ca['blinded_any_unsafe_rate']:.0%}** "
                 f"vs **rubric-aware {ca['cued_any_unsafe_rate']:.0%}** = "
                 f"**{ca['cueing_gap_pp']:+} pp cueing gap** "
                 f"({ca['flagged_by_cued_only']} cells flagged only when cued). The blinded figure is "
                 f"the headline; the cued figure is a defect-detector upper bound.")
        L.append("")
    L.append("_Generated by `python3 -m caeval.cli fixtures` — do not hand-edit. Every rate names its "
             "endpoint, aggregation and denominator; `panel-any over cells` and `mean per-judge over "
             "judge-cell evaluations` are DIFFERENT endpoints and will differ._")
    return "\n".join(L)


def render_readme_block(analyses: dict[str, dict]) -> str:
    return f"{BEGIN}\n{arms_fixture(analyses)}\n{END}"


def extract_readme_block(readme_text: str) -> str | None:
    if BEGIN not in readme_text or END not in readme_text:
        return None
    start = readme_text.index(BEGIN)
    end = readme_text.index(END) + len(END)
    return readme_text[start:end]


def splice_readme(readme_text: str, block: str) -> str:
    existing = extract_readme_block(readme_text)
    if existing is None:
        raise ValueError(f"README is missing the {BEGIN} / {END} markers")
    return readme_text.replace(existing, block)


def load_analysis(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())
