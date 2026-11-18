# Test your own clinical AI system

This guide is for a team that wants to stress-test a clinical AI product it builds,
buys or operates. It runs the system on clinical cases, then again with a key fact
removed or contradicted, and has a judge score both answers: did the system
over-commit when it should have asked or hedged, and did it stay useful?

What you get is a **screen and an evidence package**: per-case responses, scores,
a report, and a manifest anyone can verify. It is **not** a deployment-readiness
verdict, a regulatory certification, or a validated safety measurement. Every test
family is currently `experimental` (see "What you can claim" below).

Everything below is exercised end to end, offline, by
`python tools/self_service_rehearsal.py`, from a source checkout and from a clean
installed wheel, against local stand-ins for a product and a model provider.

## 1. Install

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install "clinical-ai-eval @ git+https://github.com/KAVentures/clinical-ai-eval.git@<commit>"
clinical-ai-eval capabilities        # what this build can run
```

Pin an exact commit, not a branch: the commit is part of what your results mean.

## 2. Create a project

```bash
clinical-ai-eval project init ./acme-assessment --name acme-cds
clinical-ai-eval target validate --project ./acme-assessment     # lists every unanswered question
```

Open `acme-assessment/project.yaml` and answer every field. Blank means unanswered,
and an unanswered mandatory question blocks the run. "We didn't ask" and "the
answer is no" are different, and only one is safe to plan around.

Pick a `project.mode`. It caps what the output may claim:

| mode | use it for | needs |
|---|---|---|
| `demonstration` | trying the tool | nothing |
| `internal_regression` | "did this release get better or worse?" | your real system |
| `calibrated_assessment` | a scoped claim about safety behaviour | 2 named clinical reviewers |
| `procurement_comparison` | comparing vendors on one locked pack | 2 named clinical reviewers |
| `surveillance` | periodic post-deployment checks | your real system |

## 3. Connect the system under test

### Your product over HTTP (most teams)

```yaml
subject:
  kind: http
  url: https://cds.acme.example/api/answer
  prompt_field: question               # request body: {"question": "<case text>"}
  answer_path: data.text               # where the answer is in the JSON reply
  headers:
    Authorization: "Bearer ${ACME_CDS_TOKEN}"
  timeout: 120
  retries: 3                           # transient 429/5xx/timeouts only
  extra: {}                            # constant fields to add to every request
```

- **Credentials never go in project.yaml.** Write `${NAME}`, set the variable in
  your shell, and it is expanded when the request is sent. Only the reference is
  ever saved; a literal secret under a secret-like key is replaced before
  anything is written to a workspace or an evidence package.
- `answer_path` follows dicts and lists: `choices.0.message.content` works.
- The system receives **only the case text**, never which fact was changed.

### A conversational product (patient-facing chatbots)

```yaml
target_profile:
  types: [patient_triage_chatbot]
subject:
  kind: http
  modality: conversation
  url: https://triage.acme.example/api/chat
  history_field: messages              # request: {"messages": [{"role": ..., "content": ...}, ...]}
  answer_path: reply                   # where the reply is in the JSON response
  session_field: ""                    # set if your product returns a session id to echo back
  headers:
    Authorization: "Bearer ${ACME_TRIAGE_TOKEN}"
  timeout: 120
  retries: 3
```

The whole transcript is sent on every turn. `connector test` probes the product as
a conversation: one exchange, then repeated resets to check that replies are
stable. A product whose replies differ across identical resets is reported, because
paired comparisons from it carry sampling noise or leaked state.

### A model API directly

```yaml
subject:
  kind: llm
  provider: anthropic                  # or openai, google, xai, or one you define
  model: claude-opus-5-5
  reasoning_effort: high
  max_tokens: 4000
  system_prompt: "..."                 # optional; your product's system prompt
```

Any other vendor, gateway or local server that speaks the OpenAI-compatible
format is defined inline:

```yaml
subject:
  kind: llm
  provider: my_gateway
  model: vendor/model-name
  reasoning_effort: provider_default
  providers:
    my_gateway:
      api: openai_chat_completions
      base_url: https://gateway.acme.example/v1
      api_key_env: MY_GATEWAY_API_KEY
      effort_param: ""                 # empty if the server rejects reasoning_effort
```

See [CHOOSING_MODELS.md](CHOOSING_MODELS.md) for every option.

### Responses you already have

For a system you cannot call programmatically:

```yaml
subject:
  kind: manual
  responses_file: responses.json        # relative to the project directory
```

```bash
clinical-ai-eval prompts --project ./acme-assessment --out ./acme-assessment/prompts
```

This writes every prompt the run will send (the exact case text, nothing about
what was changed) and a `responses_template.json` with the same keys. Paste each
prompt into the system, put its answer under the same key, save the file as
`responses.json`, then `run`. No network call is made to the system.

### Check the connection (one real call)

```bash
export ACME_CDS_TOKEN=...
clinical-ai-eval connector test --project ./acme-assessment
```

A missing variable, a 401, or an `answer_path` that does not resolve fails here
with the reason, instead of producing a run full of empty answers.

## 4. Choose the judge

The judge panel is a TOML file; point `panel.config` in project.yaml at it. A
relative path is looked up in the project directory first.

```toml
min_distinct_providers = 1

[[judges]]
name = "opus-5.5"
provider = "anthropic"
model = "claude-opus-5-5"
reasoning_effort = "high"
mode = "blinded"
mock = false
```

```bash
clinical-ai-eval models check --project ./acme-assessment   # offline, free
clinical-ai-eval models list  --project ./acme-assessment   # free: is the ID offered to your key?
```

Things to know:

- **A judge is part of the measurement.** Its error rate against clinicians is
  unknown until it is calibrated for your endpoint. Until then, word results as
  "the automated screen suggests".
- **Same-developer bias.** If the judge and the system under test come from the
  same developer, report that. `min_distinct_providers` counts developers
  (`vendor`), not API hosts.
- `mode = "rubric_aware"` judges are told what was changed. They are a
  high-sensitivity defect detector, reported separately, never the headline.
- The shipped panel uses **mock** judges: offline and free, but they make every
  result a software fixture.

## 5. Use your own cases

A clinician case pack is a directory with `cases.jsonl`, one case per line:

```json
{"item_id": "acme-001", "input_text": "History: ...\n\nPhysical exam: ...\n\nLaboratory results: ...\n\nImaging: ...\n\nQuestion: ..."}
```

```yaml
case_pack:
  source: ./packs/acme-v1
  pack_id: acme-v1
  version: "1"
  expected_kind: clinician_vignette
```

```bash
clinical-ai-eval pack validate ./packs/acme-v1
```

The built-in transforms remove whole labelled sections (`Laboratory results:`,
`Imaging:`, `Physical exam:`, history), so cases written in that shape get
deterministic, reproducible perturbations. For free-text cases, author the
modified versions yourself (ideally with a clinician) and import them through the
preconstructed-variant path (`YamlFamily.ingest_preconstructed_variant`).
`builtin:demo_clinician` runs the shipped demo cases and caps the run at
`demonstration`.

**Keep real patient data out of the pack** unless your governance allows it. The
pack is content-addressed and its hash travels into the evidence package.

## 6. Run, read, verify

```bash
clinical-ai-eval plan --project ./acme-assessment
clinical-ai-eval run  --project ./acme-assessment --workspace ./acme-assessment/out/v2.3.0
clinical-ai-eval verify-package ./acme-assessment/out/v2.3.0/run_missing_information
```

Each family gets a workspace with `final_report.md` (start here), `results.jsonl`,
`responses.jsonl`, `human_review.csv` (the cells routed to clinicians), and
`assessment_manifest.json`. `verify-package` re-derives the claim and checks every
hash: an edited response, score or report reads `INVALID`.

Safety and helpfulness are reported separately and never combined. A system that
refuses everything looks "safe" on over-commitment and fails the abstention guard.

## 7. Every release: compare

```bash
clinical-ai-eval run --project ./acme-assessment --workspace ./acme-assessment/out/v2.4.0
clinical-ai-eval compare --baseline ./acme-assessment/out/v2.3.0/run_missing_information \
                         --candidate ./acme-assessment/out/v2.4.0/run_missing_information
```

This lists cases newly failing, repaired, still failing and still passing. It
exits 1 if anything newly fails, so CI can gate a deploy on it. If the case pack,
judge, prompt or rules changed between runs, it reports `ENVIRONMENT_CHANGED`
rather than crediting or blaming the product.

## 8. Bring in clinicians (for anything beyond a screen)

`human_review.csv` holds the cells a clinician must see: high-severity failures,
judge disagreements, safe-to-unsafe flips. Two named reviewers rate them
independently, then:

```bash
clinical-ai-eval adjudicate --workspace <run> --reviews a.csv b.csv
```

That reports inter-rater agreement and the judge's sensitivity and specificity
against your clinicians, which is what turns an automated screen into evidence
within the audited scope.

## Cost

Each case is sent to your system twice (original and perturbed) for each
transform, and each answer is scored once per judge. For the built-in
missing-information family that is about 50 subject calls and 50 judge calls per
10 cases with one judge, and a clinician decision-support profile runs two
families. `clinical-ai-eval prompts` shows exactly how many cells a run will
make before you spend anything. Try a small pack first. Mock judges and
`kind: manual` cost nothing.

## What you can claim

| you have | you may say |
|---|---|
| mock judges or demo cases | nothing about any product; it is a software demonstration |
| a real judge, no clinician review | "an automated screen suggests ..." within this case pack |
| clinician-adjudicated review (L2) | findings within the audited scope, with agreement reported |

No result supports a deployment, procurement or regulatory decision on its own.
The report's first paragraph states the level reached; do not quote numbers
without it.
