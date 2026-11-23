# Local-first buyer workspace and agent interface

Clinical AI Eval supports a **local-only procurement evidence register**. It is
designed for hospital procurement teams, private deployments, shell automation,
and local coding agents (including Claude Code). There is **no requirement to use
SaaS, Supabase, or an online AI model** for this workflow.

This is the *first buyer-facing foundation*, not a finished hospital procurement
product, a validated clinical evaluator, or a regulatory certification.

## Quick start — works without network access

Install the project from your locally obtained checkout, preferably in a virtual
environment. No account or hosted endpoint is needed.

```bash
python -m pip install -e .
clinical-ai-eval buyer contract
clinical-ai-eval buyer init ./hospital-assessment \
  --name "Hospital AI triage procurement" \
  --intended-use "Patient-facing symptom triage" \
  --setting "Outpatient services" --jurisdiction SE
clinical-ai-eval buyer vendor-add ./hospital-assessment \
  --vendor-id vendor_a --name "Candidate A"
clinical-ai-eval buyer evidence-add ./hospital-assessment \
  --vendor-id vendor_a --kind clinical_validation \
  --file ./documents/validation.pdf --title "Submitted clinical validation report"
clinical-ai-eval buyer status ./hospital-assessment
clinical-ai-eval buyer dossier ./hospital-assessment \
  --out ./hospital-assessment/evidence-register.md
```

Every `buyer` command produces a single JSON object with `ok` and `result`
(or `error`) fields; failures use a nonzero exit code. This is the stable
subprocess interface for local agents. `buyer contract` documents available
commands and evidence types without requiring an initialized workspace.

Documents are copied into `hospital-assessment/evidence/<sha256>`, not uploaded.
Paths to source directories are not persisted. Files are private to the local
user where the filesystem supports owner-only permissions. Status recalculates
SHA-256 to flag changed/missing documents. A document's presence **does not**
establish that its claims were independently checked. The original filename,
submitted title, hash, and an initial `not_reviewed` state are recorded.

Seven default evidence categories are used for completeness prompts, not as a
claim that every organisation must collect all seven in every purchasing pathway.
There is currently no document extraction, human review UI, e-signature, SSO,
server-side authorisation, or production audit log.

### Connect the established procurement evaluator

The buyer evidence register and the existing evaluation workflow are intentionally
separate. Freeze hazards and case-pack conditions before evaluating vendors:

```bash
clinical-ai-eval procurement init ./hospital-assessment/assessment \
  --name "AI triage comparative study" \
  --families patient_red_flag --pack-id reviewed-pack-v1 \
  --pack-hash YOUR_ACTUAL_REVIEWED_PACK_HASH \
  --hazards ./hazards.json
clinical-ai-eval procurement add-vendor ./hospital-assessment/assessment \
  --vendor vendor_a --subject ./subject-vendor-a.json
clinical-ai-eval buyer status ./hospital-assessment
```

Follow [TEST_YOUR_SYSTEM.md](TEST_YOUR_SYSTEM.md) to run the declared subject,
perform blinded physician review, verify the evidence package and ingest it via
`procurement ingest-package`. The independent `procurement export` command
produces the **clinical comparison dossier**, with its own provenance,
comparability, missing-data and nonvalidated-family warnings.

The new `buyer dossier` command produces **only the administrative evidence
register**. It does not silently incorporate missing analyses or claim that a
verified procurement dossier exists.

## Running clinical evaluations locally

The existing model provider interface accepts local OpenAI-compatible inference
servers. Example judge configuration (model ID and capabilities depend on the
model actually deployed locally):

```toml
[providers.local_judge]
api = "openai_chat_completions"
base_url = "http://127.0.0.1:8000/v1"
api_key_env = ""
vendor = "local-weights-developer"

[[judges]]
name = "offline-clinical-screen"
provider = "local_judge"
model = "YOUR_LOCAL_MODEL_ID"
mode = "blinded"
reasoning_effort = "provider_default"
mock = false
```

A locally hosted product can also be connected with `subject.kind: http` or
`subject.kind: llm`. Confirm that both subject and judge endpoints resolve to
local addresses. CLI installation itself is local, but a caller can **opt into
remote services** using the existing provider configuration. Keep patient data
out of such calls unless expressly authorised. A local model is **not**
necessarily a calibrated clinical judge.

For a true air-gapped deployment, preinstall the Python dependencies, local
inference runtime, model weights and any test packs from approved media.
The buyer workspace does not need external calls. Real evaluation can use local
inference and local physician review, subject to hardware and model availability.

## Claude Code and other local CLI agents

Claude Code can invoke the commands using ordinary local shell tools. The
repository includes a `.claude/skills/clinical-ai-procurement/SKILL.md` with the
contract and guardrails. Any agent that supports subprocess execution and JSON
output can do the same. A native MCP server is **not required** to accomplish
this, and should only be added later if it materially improves integrations.

**Important:** running a CLI locally does not make a cloud-based coding agent
local. A cloud agent may transmit command output or text it reads to its provider.
Use synthetic, de-identified, or explicitly approved data with cloud agents.
For protected information, keep both the tools and inference in an authorised
private environment.

## Planned next buyer features

1. Approval-ready buyer intake with risk-sensitive checklists and pre-frozen
   clinical acceptance criteria.
2. Guided vendor invitations and evidence intake with human validation of
   extracted document claims.
3. Secured local browser UI with roles and auditable reviewers (the current
   unauthenticated loopback console is **not** safe as a shared hospital server).
4. Fully automated, integrity-checked handoff from this register into the
   procurement evaluator and back into a committee-ready single dossier.
5. Optional stdio MCP adapter with read-only defaults, bounded calls, and
   explicit approvals before sensitive writes or external inference.

The interface should remain usable **without** features 3–5: local filesystem
and documented CLI calls are first-class, not fallbacks for a cloud product.
