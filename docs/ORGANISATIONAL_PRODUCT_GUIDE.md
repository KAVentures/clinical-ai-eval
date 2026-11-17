# Using Clinical AI Eval as an organisational assessment product

**Product status: technical alpha; not a clinically validated certification tool.**
The intended customers are clinical-AI startups testing releases, healthcare organisations
evaluating their own applications, and procurement/governance teams assessing suppliers.
The researcher-oriented physician-validation study is a separate evidence-generating
workstream; **its future results cannot be presumed today**.

## What a customer actually buys or uses

- **Development and release engineering:** compare the same clinical cases across two
  product versions. Detect newly triggered unsafe-overconfidence or abstention signals
  and preserve examples for clinician follow-up. A green screen is *not* a safety approval.
- **Internal governance:** declare intended use, patient/clinician audience, failure
  modes, case selection, approved data sharing and reviewer responsibilities. Preserve
  review packets, exceptions, coverage and a independently verifiable evidence bundle.
- **Procurement:** lock a common case pack, endpoints, evaluation rules and a
  buyer-authored risk register **before** supplier execution. Compare per-hazard
  results only where the conditions and provenance really match; show missing
  evidence and uncertainty explicitly. No overall rank, automatic buy/no-buy or
  medical-device conformity claim is produced.

## The product boundary

**An evaluation contract is not a clinical ground truth.** Each assessment must
state (1) what the buyer or developer wanted to test, (2) the case population
actually exercised, (3) which evaluator was used and its measured/unknown
operating characteristics, (4) what clinicians reviewed, and (5) which
important risk classes remain unmeasured. Clinical-AI-Eval currently labels
all evaluation families experimental.

An independently verified package means its *recorded bytes and declared
definitions* match their manifest. This is tamper-detection, **not** proof that
its clinical labels are correct, that a vendor endpoint truly represents the
deployed application, or that a genuine clinician authored a review.

Data protection is a product requirement: public synthetic examples are safe
for smoke tests; real cases, provider prompts, clinical responses and reviewer
packets belong in a privately governed workspace. Don't send identifiable
patient records to vendors without proper authorisation and agreements.

## Developer workflow (no paid APIs required for setup)

```bash
python -m pip install -e ".[dev]"
clinical-ai-eval capabilities
clinical-ai-eval project init ./assessment --name my-clinical-ai
# Complete every required entry in ./assessment/project.yaml
clinical-ai-eval target validate --project ./assessment
clinical-ai-eval connector test --project ./assessment
clinical-ai-eval plan --project ./assessment
# When an approved case pack and an authorised target are configured:
clinical-ai-eval run --project ./assessment --workspace ./assessment/out
clinical-ai-eval verify-package ./assessment/out/run_missing_information
```

Generated evidence from a synthetic subject or unreviewed case pack can be used
for **demonstration or internal debugging only**, never a procurement finding.

## Buyer workflow (experimental, verification-first)

1. Buyer creates a hazard JSON array with `hazard_id`, optional `family`, and
   `acceptance_criterion`: `metric`, `operator`, `threshold`.
2. Buyer chooses a clinician-reviewed case pack, evaluator configuration and
   eligible family, and freezes a *new* procurement folder. Use **both**
   `--pack-id` and `--pack-hash` (the content hash emitted by `pack inspect`).
   The buyer flow then rejects any vendor evidence with a different case-pack
   content hash. A pack ID alone is not a content lock.
3. Each supplier is registered with an opaque label and evaluated through the
   regular project-bound run path under the exact frozen `conditions_hash`.
4. Buyer ingests independently verified run packages (not editable CSV scores).
5. Buyer exports an exploratory, per-hazard committee dossier.

```bash
clinical-ai-eval procurement init ./buyer-study --name "Clinical assistant pilot" \
  --families missing_information --pack-id frozen-v1 \
  --pack-hash YOUR_FROZEN_CASEPACK_CONTENT_HASH \
  --hazards ./buyer-hazards.json
clinical-ai-eval procurement add-vendor ./buyer-study --vendor supplier-a \
  --label "Product A" --subject ./supplier-a.json
# The run itself MUST record buyer-study's conditions_hash.
clinical-ai-eval verify-package ./supplier-a-workspace
clinical-ai-eval procurement ingest-package ./buyer-study \
  --vendor supplier-a --workspace ./supplier-a-workspace
clinical-ai-eval procurement compare ./buyer-study --family missing_information
clinical-ai-eval procurement export ./buyer-study --out ./buyer-dossier.md
```

If a mandatory provenance hash is unavailable, the comparison is
`INCOMPARABLE`; the buyer gets a gap to resolve, **not** an inferred match.
`record_result()` accepts caller-supplied fixture rows for development and is
not evidence ingestion. Its output is visibly unverified and may not be
presented as a validated procurement comparison.

**Known alpha limitations:** this is a local CLI plus a loopback-only,
unauthenticated operator console; it has no production multi-tenant IAM,
supplier portal, hospital integration certification, or independent end-to-end
clinical case-pack validation. Treat it as on-premise pilot tooling, not a
hosted hospital service.

## What constitutes product readiness

Before a hosted beta: complete authenticated organisation workspaces and RBAC;
reliable target/vender connection onboarding; case-pack content and version
locking; signed clinician roles; reviewer-friendly adjudication; strict PHI
separation; provider cost caps and retries; verified evidence ingestion;
regression and multi-vendor comparison; explicit coverage/uncertainty reports;
supportable deployment/upgrade documentation and end-to-end buyer acceptance
tests.

Before *clinical* validation claims: prospectively validate each claimed
family and evaluator endpoint against blinded physicians and external data.
Those are separate scientific gates and must not block honest use as an
**experimental** developer/internal-assurance product.
