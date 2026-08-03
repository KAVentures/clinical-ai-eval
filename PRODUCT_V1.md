# PRODUCT_V1 — what this is, who it is for, and what it may claim

Status: **v0.7, self-service technical alpha.** This document defines the product
and, more importantly, its **claim boundaries**. Where it and marketing language
disagree, this document wins; where it and the code disagree, the code wins.

## The one-sentence product

> Connect your clinical-AI product, declare its intended use, run the test families
> that intended use justifies, involve your own clinicians where the claim requires
> it, and receive an auditable evidence package — without assistance from the
> repository author.

## Two audiences

**Health-AI teams** — repeated use during development: regression after a model,
prompt, RAG or guideline change; comparing repaired vs unrepaired versions; CI/CD
gating; a versioned safety-evidence history.

**Procurement and governance teams** — evaluating a vendor product against a
standardised protocol; comparing products on identical cases and endpoints;
inspecting unresolved risk, evaluator disagreement and clinical-review results;
obtaining a signed package for an assurance committee.

## THE TWO LAYERS OF EVIDENCE (the central product decision)

Confusing these is the failure mode this product exists to prevent.

| layer | question | established by | current state |
|---|---|---|---|
| **Platform evidence** | Does the harness detect the failures it claims to detect? | controlled validation studies (Track B) | **none — no family is validated** |
| **Product evidence** | How did *this* product do on *this* locked case pack? | a customer run | available today |

Without platform evidence, product numbers are untrustworthy. Without product
evidence, platform validation says nothing about the customer's system. **A run
may never present product evidence as though the platform evidence existed.**

## Run modes and permitted claims

The mode is declared in `project.yaml` and enforced (`caeval/project.py`). A mock
subject can only support `demonstration`; `calibrated_assessment` and
`procurement_comparison` require ≥2 named clinical reviewers.

| mode | label emitted |
|---|---|
| `demonstration` | `DEMONSTRATION — NOT CLINICAL EVIDENCE` |
| `internal_regression` | `INTERNAL REGRESSION SCREEN` |
| `calibrated_assessment` | `CALIBRATED ASSESSMENT WITHIN THE STATED SCOPE` |
| `procurement_comparison` | `COMPARATIVE PROCUREMENT EVIDENCE — NOT REGULATORY CERTIFICATION` |
| `surveillance` | `POST-DEPLOYMENT SURVEILLANCE SCREEN` |

A run's claim is the **weakest** of: run mode, run conformance level (L0/L1/L2),
and the **maturity of each family used** (`caeval/maturity.py`). All three gates
are enforced in code, not by convention.

## What this product will NEVER output

- a deployment-readiness verdict, or "safe / not safe";
- a single collapsed safety score;
- a regulatory-compliance certificate, or an AI Act / MDR classification;
- a buy / do-not-buy recommendation;
- a clinical performance claim outside the declared intended use;
- a finding from a family whose maturity does not support one.

The EU AI Act places obligations around risk management, documentation, logging
and human oversight on relevant high-risk systems. This platform can generate
**supporting evidence** for those obligations. It cannot determine legal
classification or certify compliance, and it asks for regulatory status as an
input rather than inferring it.

## Standards posture: crosswalks, not certificates

An evidence package may **map** its contents to NIST AI RMF, IMDRF GMLP, NICE ESF,
NHS DTAC, WHO guidance and the EU AI Act's documentation obligations. A crosswalk
states *which evidence exists*, never *that a requirement is met*. Every crosswalk
row must distinguish: vendor assertion · document reviewed · independently tested ·
not tested · **contradicted by testing**.

## Honest current state (v0.7)

Implemented and adversarially tested: family SDK with capability gating; hazard
registry with predeclared acceptance criteria; per-family maturity gates; blinded
vs rubric-aware evaluation with the cueing gap reported; fail-closed multi-judge
quorum; case-clustered CIs; private vault with an authorized, audited access
boundary; preregistration with lock-hash verification; deterministic certificate
verifier and minimum-information solver; real self-service intake.

**Not yet true:**
- **No family is validated.** Both runnable families are `experimental`.
- No real-judge L1 or real-clinician L2 run has been completed.
- Patient-facing evaluation is not implemented and fails closed.
- Clinical review is CSV-based; there is no browser review UI.
- There is no API server, web UI, tenancy, RBAC or scheduled surveillance.
- No validated public case packs exist, so users can operate the machinery but
  cannot inherit a defensible measurement claim.
- The vault boundary is fail-closed and audited; it is **not** hospital-grade IAM.

## Release gates

| release | done when |
|---|---|
| **0.7** self-service alpha | real intake wizard; user target files; connector dry-run; resumable runs; local UI; one-command package verification; **no code edits required** |
| **0.8** clinical workflow beta | case-pack authoring; browser review; validity review; blinded safety review; adjudication; role separation; private vault; calibration workflow |
| **0.9** procurement beta | vendor questionnaire; supplier/buyer roles; comparison; evidence-claim matrix; standards crosswalk; **no single safety score** |
| **1.0** validated scoped release | ≥1 family validated prospectively; independently implemented defects; blinded clinician reference labels; held-out cases; published sensitivity and false-alert rates; external replication; documented applicability boundaries |

At 1.0 the defensible claim is:

> "Clinical AI Eval is a self-service assurance platform. **Specific test families
> have measured validity for specific intended uses.** Results outside those
> validated scopes are explicitly labelled exploratory."

Never:

> "Clinical AI Eval proves that a healthcare AI system is safe."

## The onboarding acceptance test (the real 0.7 definition of done)

A team unfamiliar with this repository must be able to: clone → one command →
create a project → connect an endpoint → choose a case pack → run → assign two
reviewers → adjudicate → export → **independently verify the package** — without
contacting the author and without editing Python.

Until that passes end to end, this is a developer framework, not a product.
**Current status: partially met** — project init, intake validation, connector
dry-run and evidence packages work; browser review, one-command verification and
container distribution do not yet exist.
