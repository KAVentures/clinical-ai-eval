# clinical-ai-eval — reference harness for the EVAL_STANDARD.md protocol

A runnable, offline reference implementation of a **candidate protocol** (not a
validated standard) for perturbation-robustness screening of **text-based,
clinician-facing** clinical decision-support systems. Two probe families are
implemented — **missing-information** and **conflicting-evidence**. It turns the
upstream research repos' published methods into a reproducible process: intake →
suite selection → controlled perturbations → **structural validity pre-filter** →
**paired** scoring → **fail-closed multi-judge** panel → disagreement → **blinded
human-review + validity queues** → evidence package → L2 adjudication.

The primary output is a **screen plus an evidence package, never a
deployment-readiness verdict** (§0). Every report says so in its first paragraph and
declares its conformance level.

## ⚠️ Scientific status (read first)
This is an **alpha protocol + reference implementation**, not a validated safety
harness. Current scientific validity is low and deliberately disclosed:
- The offline demo uses **mock judges + a constructed mock subject**; its numbers
  are **software fixtures** that show the pipeline wiring, **not** evidence the
  harness detects clinical safety (the self-validation is circular by construction).
- The automated validity audit is a **structural pre-filter**, not clinical
  validation; clinical load-bearingness/determinacy are confirmed only by clinicians.
- **Patient-facing evaluation is not implemented and now fails closed**: the patient
  bar names high-severity fields the scoring schema cannot produce, so a
  patient-facing profile yields zero runnable suites rather than a bogus score.
- No real-judge L1 or real-clinician L2 run has been done. Do **not** describe this
  publicly as a standard, a validated harness, or an L2 framework, and do **not**
  publish results from the current version without the corrections in
  [`CORRECTIONS.md`](CORRECTIONS.md) and independent clinician validation.
See [`CORRECTIONS.md`](CORRECTIONS.md) for the latest corrections (see the newest section at the top).

## What makes this conformant (the two non-negotiables, §0)
1. **Safety and helpfulness are scored separately and never collapsed.** The report
   never emits one "safety score"; it reports safety, helpfulness, and the
   `excessive_abstention` guard as separate axes, per judge.
2. **The evaluator is part of the measurement.** ≥2 *different-provider* judges,
   per-item disagreement reported, and a human-review queue for any headline. The
   `≥2 distinct providers` rule is enforced at runtime from `configs/judge_panel.toml`.

## Evaluate YOUR product (self-service — no source edits)
```bash
clinical-ai-eval project init ./my-assessment --name my-product
#   answer every field in my-assessment/project.yaml  (blank = unanswered = BLOCKED)
clinical-ai-eval target validate --project ./my-assessment
clinical-ai-eval connector test  --project ./my-assessment   # dry-run before spending
clinical-ai-eval plan            --project ./my-assessment
```
The intake is **fail-closed**: every mandatory question blocks planning while blank,
because "we didn't ask" and "the answer is no" are different. The run **mode**
gates what the output may claim — a `mock` subject can only support
`demonstration`, and `calibrated_assessment` / `procurement_comparison` require ≥2
named clinical reviewers. See [`PRODUCT_V1.md`](PRODUCT_V1.md) for claim boundaries.

## Quick start (offline, no keys, no downloads)
```bash
cd clinical_ai_eval
python3 -m caeval.cli demo      # intake + paired multi-judge run + mock L2 adjudication
python3 -m caeval.cli arms      # harness self-validation across three subject arms (§12)
python3 -m caeval.cli inspect   # profile, suite selection, and judge-panel status
python3 -m unittest discover -s tests_unit -t . -p 'test_*.py'   # full self-test suite
```
`demo`/`arms` use a **synthetic (mock) judge panel** and a **deliberately-defective
mock subject**. Everything is clearly labeled synthetic and is `NON_CONFORMANT` for
any claim — it exercises the full L1+L2 machinery so the pipeline is demonstrably correct.

## The §10 stages (disk workspaces, generation separable from judging)
```bash
python3 -m caeval.cli init  --workspace out/ws            # scaffold target.yaml + keys template
python3 -m caeval.cli run   --arm flawed --workspace out/ws   # generate responses + score + package
python3 -m caeval.cli run   --family conflicting_evidence --arm flawed   # the 2nd family
python3 -m caeval.cli judge --workspace out/ws --panel configs/judge_panel.toml  # re-score frozen responses (swap panel, no re-gen)
python3 -m caeval.cli adjudicate --workspace out/ws --mock    # L2: agreement + judge-vs-human sens/spec
python3 -m caeval.cli report --workspace out/ws              # re-emit the evidence package
```
`judge` re-scores the **frozen** subject responses with a different panel without
regenerating them — the cost-saving path (§7). `adjudicate` ingests filled
`human_review.csv` files, computes inter-rater agreement and judge-vs-human
sensitivity/specificity/PPV, and upgrades the run to **L2** within audited scope.

### The self-validation result (§12 steps 4–6) — SOFTWARE FIXTURE, not a finding
`arms` runs the same battery against three versions of the subject. Because the mock
judges, the mock subject, and the assertions were authored together, this
demonstrates only that the **pipeline wiring** represents three outcomes (unsafe
commitment / genuine repair / safety-by-refusal) — it is **not** evidence the harness
detects clinical safety. Non-circular validation needs clinician-specified defects
and blinded evaluators (see [`CORRECTIONS.md`](CORRECTIONS.md)).

All rates below are **generated**, never hand-typed (`python3 -m caeval.cli fixtures`;
CI fails if they drift). Each names its **endpoint, aggregation and denominator** —
`panel-any over cells` and `mean per-judge over judge-cell evaluations` are different
endpoints and legitimately differ, so neither is ever called just "the unsafe rate".

<!-- BEGIN GENERATED: arms-fixture -->
| arm | unsafe — panel-any (cells) | unsafe — mean per-judge (judge-cells) | harmful_tx (panel-any) | identifies_missing | excessive_abstention (variants) | (originals) |
|---|---|---|---|---|---|---|
| flawed | 36% (14/39) | 32% (25/78) | 36% | 23% | 0% | 0% |
| repaired | 0% (0/39) | 0% (0/78) | 0% | 100% | 0% | 0% |
| over_abstaining | 0% (0/39) | 0% (0/78) | 0% | 0% | 100% | 100% |

**Evaluator cueing (flawed arm).** Endpoint `panel_any_unsafe` over 39 auto-screened cells: **blinded 36%** vs **rubric-aware 100%** = **+64.1 pp cueing gap** (25 cells flagged only when cued). The blinded figure is the headline; the cued figure is a defect-detector upper bound.

_Generated by `python3 -m caeval.cli fixtures` — do not hand-edit. Every rate names its endpoint, aggregation and denominator; `panel-any over cells` and `mean per-judge over judge-cell evaluations` are DIFFERENT endpoints and will differ._
<!-- END GENERATED: arms-fixture -->

The `over_abstaining` row is the point: a system that just refuses everything scores
"safe" on overconfidence but lights up the `excessive_abstention` guard — which is
exactly why safety and helpfulness must not be collapsed.

## Going to L1 (real automated screen) and L2 (findings)
1. Put a git-ignored keys file next to the package (`API_KEYS.local.md`) with lines
   like `OPENAI_API_KEY = ...`, or set `MEDROBUST_KEYS_PATH`.
2. Edit `configs/judge_panel.toml` to a real panel with **≥2 different providers**
   (the primary pair must not be two models from one provider — §7). The
   `≥2-distinct-provider` rule is enforced at runtime *before* keys are even loaded.
3. Point the subject at your product with a `--subject` JSON spec (adapters:
   `mock` / `openai` / `anthropic` / `xai` / `google` / `http` / `manual`), e.g.
   `{"kind":"http","url":"https://.../answer","prompt_field":"q","answer_path":"data.text"}`.
   A real subject receives **only** the perturbed case text — never the perturbation metadata.
4. `run` → send `human_review.csv` to clinicians → `adjudicate --reviews a.csv b.csv`.
   L1 conclusions must be worded "automated screen suggests," never as findings;
   **L2** (findings within audited scope) requires the queue adjudicated with
   inter-rater agreement reported.

## Test families (SDK — `python3 -m caeval.cli families`)
Families are schema-first plugins (`caeval/family_sdk.py`). Each declares its
capabilities; the runtime **refuses to run** one this build cannot support, so
breadth is declarable without pretending unsupported modules work.

| family | maturity | status |
|---|---|---|
| `missing_information` | experimental | runnable |
| `conflicting_evidence` | experimental | runnable |
| `patient_red_flag` | experimental | **BLOCKED** — needs red-flag schema, multi-turn, escalation grading |
| `decision_certifiability` | experimental | **BLOCKED** — verifier + solver exist (v0.6), but rule bundle, provenance and action extraction do not |

## Evidence-grounding layer (`caeval/certificates/`, v0.6)
The deterministic side of the deterministic-vs-judge split: emits
`CERTIFIED_CONDITIONAL / DEFER / BLOCK` for a proposed action against a
version-pinned rule bundle, plus the minimum additional information that would
resolve a DEFER.

Two axes are **separate and both required** — `severity` (clinical importance,
never decides the verdict) and `certificate_effect` (`block|defer`, the only
verdict axis). Conflating them is what let a *present* contraindication certify in
an earlier implementation when severity was spelled `"high"`. Unrecognized values
escalate and are reported; nothing can be silenced by spelling.

The minimum-information solver is the classical **Minimum Test Set / Test Cover**
problem (NP-hard) — exact for small instances, greedy `O(log n)` above a limit,
with optional cost weighting since a records lookup and an invasive procedure are
not one unit each. `[()]` (nothing required) and `[]` (unresolvable) are distinct.

**The `decision_certifiability` family remains BLOCKED.** Implementing a verifier
does not make a measurement valid: rule bundles, provenance chains, action
extraction and clinician-authored critical-question sets do not exist yet.

## Private vault + validation study (Tracks A/B)
```bash
export CAEVAL_VAULT=/path/to/PRIVATE/vault     # separate repo or encrypted volume
python3 -m caeval.cli vault                    # metadata only — never case content
python3 -m caeval.cli study --init             # preregistration template
python3 -m caeval.cli study --lock             # freeze the analysis plan
```
The vault enforces blinding structurally: the evaluated system sees only the facing
input, blinded judges see case+response, rubric-aware judges additionally see the
defect spec, and **defect labels are refused until the analysis plan is locked**.
The study scaffold fails closed on unfilled role slots — dry runs, schema
validation and packet generation still work; only a *validation finding* is refused.
Role separation is enforced: the defect implementer must be independent of the
hazard authors, and an adjudicator who constructed defects is not blind.

## Implemented test families
- **`missing_information`** (§12 reference family) — remove_labs/imaging/exam, make_minimal_hpi, renal-dosing.
- **`conflicting_evidence`** — canonical `add_conflict`; detects whether the system flags an injected contradiction.

Other families named in the profile table are reported **REQUIRED-BUT-NOT-RUN** with
a concrete `blocked_reason` (they need a RAG corpus, scribe transcripts, a
patient-triage case bank, two product versions, or a consistency-scoring mode) —
scope is honest, never silently dropped.

## Layout & provenance (§11)
Each module names the ONE upstream implementation it canonicalizes:

| module | canonical source |
|---|---|
| `caeval/perturbations.py` | clinical-evidence-sufficiency-llm/src/perturbations.py |
| `caeval/validity.py` | health-ai-readiness-robustness/scripts/perturbation_validity.py |
| `caeval/score.py` + `prompts/judge_prompt.txt` | clinical-evidence-sufficiency-llm/src/score_outputs.py + prompts/judge_prompt.txt |
| `caeval/providers.py` | clinical-ai-reconciliation/judge/providers.py |
| `caeval/disagreement.py` | clinical-ai-reconciliation/judge/export_disagreement.py |
| `caeval/reliability.py` | clinical-evidence-sufficiency-llm/src/reliability.py |
| `caeval/review.py` + `caeval/blinding.py` | clinical-ai-reconciliation/judge/sample_human_study.py + judge/blinding.py |
| `caeval/harm_ontology.py` | Gu et al., Nature Medicine (health-AI-readiness) Table 1 |

```
clinical_ai_eval/
  EVAL_STANDARD.md            # the protocol (INTENT; where it disagrees with code, code wins — §14)
  selection_rules.yaml        # §4 rule-based suite selection (inspectable, with blocked_reason)
  configs/judge_panel.toml    # §7/§11 panel; ≥2-distinct-provider rule read from here
  prompts/judge_prompt.txt    # §6 judge rubric (+ excessive_abstention guard)
  tests/<family>/family.yaml  # §3 test families (missing_information, conflicting_evidence)
  pyproject.toml, requirements.txt   # packaging; console script `clinical-ai-eval`
  caeval/                     # harness package: perturbations, validity, score, providers,
                              #   disagreement, reliability, stats, checks, review, blinding,
                              #   harm_ontology, intake, selection, subject, workspace,
                              #   pipeline (generate/score/analyze), adjudicate, report, cli
  targets/                    # synthetic battery + deliberately-defective mock subject (§12)
  tests_unit/                 # stdlib unittest self-tests
  out/                        # run workspaces / evidence packages (git-ignored)
```

## Scope & honesty
Two families are implemented end-to-end; the rest are reported **REQUIRED-BUT-NOT-RUN**
with a concrete blocker, never silently dropped (§4, §13). Validity labels are
automated at L1 (disclosed). **No directional claim is made about evaluator
operating points**: the earlier "high-sensitivity / low-specificity screen" wording
was RETRACTED in v0.3 (see `CORRECTIONS.md`) — with a *blinded* judge the measured
specificity was 1.0, and the over-flagging belonged to the *cued* evaluator.
Sensitivity/specificity/PPV are reported only once measured against clinician
labels at L2, per endpoint, with the positive class stated. See
`out/<run>/limitations.md` — restated every run. Requires Python 3.9+, `numpy`,
`pandas`, `pyyaml`; real judges/subjects call HTTP APIs via stdlib `urllib` (no SDKs
required). No network for the demo. Not done by design: pointing the subject at the
live TravelDoctor EU / riktlinjer.kinvectum.com endpoints (§12 step 2).
