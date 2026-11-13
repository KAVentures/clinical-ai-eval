# Published-data consistency audit (read-only, 2026-10-23)

**Scope:** verification of previously published, versioned data from two independent prior papers. This is NOT newly generated clinical evidence and does NOT validate the uncompleted 150-case physician-validation study.

## Exact source checkouts

- `KAVentures/health-ai-readiness-robustness`, main commit `48c6b67102e4494bf224f63fd9a61816aa9da920`.
- `KAVentures/clinical-evidence-sufficiency-llm`, main commit `b5e5818aa704200f6491779157bde4f1eba2990e`.

## Connector-read cross-checks completed

1. Read the three binary annotation files directly. All have **50 unique** blinded `annotation_id` rows with identical coverage. Positive ratings: investigator 27/50, clinician O 26/50, clinician G 35/50. These match the published three-rater summary rates.
2. Read the 50-item blinded mapping and the saved **200 model-item judge-vote records**. All 50 mapped items have matching judge-vote records; no key lookup failed. Each judge record exposes the four expected provider voters.
3. Read the previous evidence-sufficiency primary table and JSON analysis summary. Standard condition: **592/1200** automatically labeled unsafe; evidence-sufficiency condition: **296/1200**; reported arithmetic **absolute difference 0.2466666667** matches the JSON.
4. Reviewed the earlier study's provenance documentation. Its two independent physician labels and investigator labels are genuinely distinct; separate blank template packets are not mislabeled as completed reviews.
5. The new PR's tests, wheel, capability, fixture and smoke workflows passed in GitHub CI, across Python 3.9, 3.11 and 3.12.

### Remaining re-execution verification

- Generate the SHA-256 digest report via `tools/audit_prior_evidence.py` from clean local checkouts using the frozen commit IDs above. The code is covered by synthetic fixture tests, but the **full local-file CLI audit was not yet executed against both complete checkouts in this PR**. The connector-read checks above are a direct, narrower verification.
- The former study's primary raw response record set is not part of the small released reports used here. Thus the existing 1,200-cell result **has not been recomputed from original private response texts by this audit**.
- Before submission, verify the released Clinical-AI-Eval implementation smoke test and package verifier independently of this historical-data audit; the evidence audit is not a substitute.

### Scientific interpretation

These checks improve confidence that prior annotation keys, judge votes and published aggregate numbers are preserved consistently. They do not establish clinical accuracy of the framework, judge calibration on new cases, patient safety, or novel research findings. Cite the original two preprints whenever the prior outcome numbers are mentioned.
