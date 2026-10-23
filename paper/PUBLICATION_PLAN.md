# Lowest-cost publication track (October 2026)

**Status: new proposed manuscript track; NOT an amendment to the physician-validation protocol.**  
**Owner:** investigator. **Objective:** finish a defensible research-software/methods paper without repeating expensive clinical model calls or requiring new physician ratings.

## Decision

Submit a *software/methods* paper on the **existing \`clinical-ai-eval\` research framework**, with zero-cost, explicitly retrospective illustrations from prior published studies. The original prospective [physician-validation study](https://github.com/KAVentures/clinical-ai-eval-physician-validation) remains a distinct, uncompleted protocol. Do not merge its original 3-physician prospective requirements into this paper.

### Facts verified as of October 23, 2026

- The framework has deterministic offline synthetic smoke fixtures, multiple test families and an evidence-package verifier; it reports all families as experimental.
- It has **not** established real-world clinical sensitivity/specificity for these test families. In particular, the runnable patient and RAG demonstrations are not clinically validated.
- The dedicated physician-validation repository has no completed real-study result set. Its [open PR #1](https://github.com/KAVentures/clinical-ai-eval-physician-validation/pull/1) describes an even larger three-physician experiment (960 response ratings plus construct review and external replication). CI success only proves software checks passed, not that any physician validated the study.
- Already public, previously published datasets relevant for **illustration only**:
  - [arXiv:2607.18828](https://arxiv.org/abs/2607.18828): 50 source-item human labels from the author and two independent clinicians; 200 model-item rows with four provider judges; saved summary and key.
  - [arXiv:2607.18086](https://arxiv.org/abs/2607.18086): 1,200 paired model-item responses scored in each prompt arm, with published rate table and summary JSON; three-clinician review reported in that *prior* work.
- Neither prior dataset is automatically a validation cohort for the proposed 150 HealthBench Professional cases; endpoints, provenance, sample selection and model prompts differ. A retrospective recomputation cannot be described as fresh, prospective, independent validation.
- [PrecepTron](https://github.com/2v/PrecepTron), arXiv:2609.12822, validates task-specific physician-trained rubric judges. The released adapters are not directly calibrated for the framework's binary unsafe-overconfidence construct. Its local 32B inference or LoRA training is **not in scope** for the low-cost completion route.

## Stop-doing list

- No 150 × 2 × 4 fresh target inference campaign.
- No 480/960 manual clinician ratings.
- No new commercial multi-judge panel.
- No paid GPU to run PrecepTron.
- No claims of physician agreement obtained from the mock demonstrations.
- No relabeling prior published analyses as results of a new experiment.
- No post-hoc migration of the prior results into a purported prespecified 150-case protocol.

## Automation to complete

1. **Offline audit prior evidence** (script provided in this PR). Clone the two *already-public* preprint repositories as adjacent directories, then run:

   \`\`\`bash
   python tools/audit_prior_evidence.py \
     --readiness-root ../health-ai-readiness-robustness \
     --sufficiency-root ../clinical-evidence-sufficiency-llm \
     --out paper/prior_evidence_audit.json
   \`\`\`

   It validates binary clinician-label provenance, identifier consistency, judge-vote coverage and published aggregate arithmetic; emits SHA-256 content hashes. It calls **no providers**, downloads **no clinical records**, and does **not** reproduce unpublished/raw responses. The audit intentionally cannot prove full raw-trial reproducibility.

2. **Exercise the actual framework from a clean installation** and save version-pinned outputs:

   \`\`\`bash
   python -m pip install -e ".[dev]"
   python -m pytest tests_unit -q
   python -m caeval.cli capabilities --check
   python -m caeval.cli fixtures --check
   python -m caeval.cli demo
   python -m caeval.cli arms
   \`\`\`

   Use CI logs plus the generated capability table to distinguish supported, merely declared and blocked families. Never interpret synthetic fixture rates as clinical findings.

3. **Create final research-software manuscript** from \`paper/PAPER_DRAFT.md\`, audit outputs and current capability declarations. Generate architecture figure automatically from code paths, and package example output and its evidence-verification result. Include a provenance/limitations table stating which demonstrations are synthetic vs previously published vs independent.
4. **Finish packaging/documentation checks:** installable wheel, offline demo, command reference, example verify-package, meaningful warnings, OS support, versions, citations, code license and data-redistribution restrictions.
5. **Human signoff limited to:** check author identity and conflict/AI disclosures, verify the scientific conclusions and literature, confirm licensing/governance, and approve submission. Recheck every asserted metric against generated output.

## Scientific acceptance criteria

- The primary contribution is reproducible *software engineering and measurement design*, not a newly established clinical safety rate.
- The proposed framework's limitations are not buried: all current families experimental; physician calibration absent for the framework; synthetic demos are not clinical evidence.
- A genuine end-to-end, no-network example runs from a clean environment and produces an auditable evidence package.
- Prior published findings used illustratively are cited to their own preprints, not reported as a new cohort or independent replication.
- Executable reproducibility commands and version pins are disclosed. Never commit benchmark text, patient information, credentials or raw licensed clinical cases.
- Retrospective audit must pass; failing files, counts or versions are investigated, not silently skipped.
- A novel prospective experiment is added **only** if manuscript review identifies a unique, testable gap; then separately preregister scope and authorize a hard dollar/API-call cap in advance.

## Publication destinations

- **Near term:** methods/software preprint, after local verification and author signoff.
- **Peer review:** select a suitable research-software venue once the package genuinely satisfies scope and completeness requirements.
- **JOSS caveat:** current JOSS policy requires a public open-development history of >6 months with evidence of sustained activity, demonstrated research use, and feature completeness. The core repo's public creation date is July 22, 2026, so an October 2026 JOSS submission would be premature. An arXiv preprint does not need to wait for that history.
- Declare AI assistance accurately. Under JOSS's current policy, AI may assist in software/code/documentation but the author must review outputs, retain core design judgment and provide an AI use disclosure.

## Deferred / optional, not publication blockers

- Single-investigator prospective clinical labeling and test–retest reliability.
- Further medical model comparisons under new data.
- PrecepTron-32B-Management task-specific evaluation; adaptation to unsafe-overconfidence requires a separate validation experiment.
- Original three-physician protocol and external Real-POCQi cohort.
- A claim of calibrated or externally replicated clinical evaluation.

**Budget target for the above track:** $0 of incremental model API/inference charges. This does not include ordinary publication expenses, should a paid journal be chosen. This is a scope target, not a claim that the software is already publication-ready.
