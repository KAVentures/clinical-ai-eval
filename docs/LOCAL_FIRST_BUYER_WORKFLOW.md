# Local-first buyer workspace and agent interface

Clinical AI Eval supports a **local-only procurement evidence register**. It is
designed for hospital procurement teams, private deployments, shell automation,
and local coding agents (including Claude Code). There is **no requirement to use
SaaS, Supabase, or an online AI model** for this workflow.

This is the *first buyer-facing foundation*, not a finished hospital procurement
product, a validated clinical evaluator, or a regulatory certification.

## Local web application — OWG-style launch

The buyer dashboard runs as a background-capable local HTTP process and opens in
the default browser. There is **no separate hosted frontend** and no cloud account.

```bash
clinical-ai-eval buyer serve ./hospital-assessment
```

If this directory is new, the local browser shows a setup wizard to define the
assessment; otherwise it opens the existing buyer workspace. The interface
supports vendor registration and local evidence uploads, a vendor/evidence
inventory, integrity status and next-action guidance. It never sends the uploaded
file to an AI provider.

The server binds **only to `127.0.0.1`**, on an OS-selected free port by default,
and gives the browser a random per-process local session token. It enforces an
exact loopback Host, SameSite session cookie, origin checks on forms, no-store
headers and a restrictive CSP. It serves no documents themselves, only their
metadata and integrity status. The initial launch URL should be treated as a
local session secret. Closing the terminal stops the server.

For an agent, script or custom desktop launcher:

```bash
clinical-ai-eval buyer serve ./hospital-assessment --no-browser --port 8766
```

This single-user prototype is **not** authenticated multi-user hospital software.
Never expose the localhost port through a reverse proxy, VPN tunnel, network
binding or cloud service. Production shared use needs proper identity, role
permissions, access logging, TLS and a dedicated review of local API threats.

A packaged click-to-launch installer and persistent background service are
future delivery work; the current CLI starts the local browser app.

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

The `buyer dossier` command and browser's **Download combined evidence dossier**
link now produce a single local Markdown report, separating the administrative
inventory from the clinical assessment. The report reconstructs clinical
comparisons from the originally ingested packages in a private temporary
workspace: it checks the original manifest digest and re-runs package
verification and ingestion. It excludes missing, changed, caller-supplied or
out-of-scope evidence, identifies shortlist candidates without testing, and
does not trust editable cached result cells. It does not mutate the frozen study.

The report is exploratory. A reverified package does not establish that a
vendor actually produced its responses, that its claims are true, or that the
clinical evaluation method is validated. Keep original run workspaces available
locally; if moved or missing, the report will disclose the gap. The downloaded
report may contain confidential assessment metadata; share only with approved
recipients.

## Offline vendor disclosure exchange

In the buyer dashboard, download a candidate-specific questionnaire, transfer it
through an approved procurement channel, and upload the completed JSON. No vendor
account, internet access or third-party API is required for this exchange.
The request contains the buyer's intended use, setting, jurisdiction and candidate
identity. It excludes competing vendors, evaluation case packs, hazard thresholds,
documents, clinical results and local paths. Review the template before sharing.

The same workflow is callable by local agents:

```bash
clinical-ai-eval buyer questionnaire-template ./hospital-assessment --vendor-id vendor_a
clinical-ai-eval buyer questionnaire-import ./hospital-assessment --vendor-id vendor_a --file completed.json
```

The template command returns the standard CLI JSON envelope; the questionnaire
file for exchange is the object in its `result` field. A vendor completes the
submission while preserving the request exactly. Each question needs an explicit
`provided`, `not_available`, or `not_applicable` response and an answer/explanation;
references are optional strings. The exact request schema and buyer scope are
hash-bound. Imports reject a different candidate, altered scope, changed question,
missing/duplicate answer or oversized/malformed JSON. The original response bytes
are stored in the content-addressed evidence register and rechecked on status/export.

All statements, respondent identities and product versions are **vendor self-reported,
unverified claims**. The hash binds the questionnaire to the local scope, not the
sender's identity; no signature or independent corroboration is implied. A supplied
answer never closes clinical, security or regulatory evidence requirements, and
references are never automatically fetched. The questionnaire is an administrative
intake aid, not a validated regulatory checklist or a clinical test. Its provenance
and limitations are shown separately in the combined report.

## Research workspace

Open **Research workspace** from the local procurement dashboard to see the live
scientific family registry, runnable/blocked status, maturity and executor
information. This view uses the same registry as the research CLI.

For read-only verification of existing runs, an approved local operator can
copy complete run directories to `<buyer-project>/research-runs/<run-id>/`.
Run IDs use lowercase letters, digits, hyphens and underscores. The browser
does not accept arbitrary server paths or uploaded executable artifacts.
The inspection checks the manifest schema and artifact inventory, limits
package size and file count, rejects symlinks and unsafe manifest paths, and
invokes the existing scientific verifier. It does not alter staged files.
Raw clinical responses are not exposed by this overview.

The same read-only view is available to CLI agents:
```bash
clinical-ai-eval buyer research ./hospital-assessment
```

A valid manifest is evidence of artifact integrity, not authenticated provenance
or independently validated clinical performance. The research CLI, study
protocols, scoring and publication exports remain unchanged. This initial
view is not yet a full browser experiment designer or multi-user review tool.

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
4. Guided clinical run configuration, execution and review in the browser;
   the combined report already reconstructs results from reverified packages.
5. Optional stdio MCP adapter with read-only defaults, bounded calls, and
   explicit approvals before sensitive writes or external inference.

The interface should remain usable **without** features 3–5: local filesystem
and documented CLI calls are first-class, not fallbacks for a cloud product.
