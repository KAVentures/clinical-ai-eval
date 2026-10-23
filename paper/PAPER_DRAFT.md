# Clinical-AI-Eval: An Auditable Framework for Perturbation-Based Assessment of Medical Language Models

**Manuscript status: AUTHOR-REVIEW DRAFT, 23 October 2026.** This is a proposed *software/methods* article, NOT the previously proposed three-physician clinical-validation experiment. Software claims, experiment scope and citation accuracy must be checked against the locked release before public submission. Do not call it a validated clinical safety evaluator.

## Abstract

**Background:** Static medical language-model benchmarks can conceal failures that emerge when relevant evidence is withheld or internally contradictory. Automated response grading can scale evaluation, but the grader is another fallible model, and apparently low safety-failure rates can reflect over-refusal, rubric cueing or evaluator preferences.

**Objective:** To describe an open-source research framework for paired clinical-evidence perturbations, blinded automated evaluation, separate safety and helpfulness reporting, source-level provenance, and verification of generated assessment packages.

**Software:** Clinical-AI-Eval provides structured test-family definitions, case/input perturbation and validation contracts, interchangeable model interfaces, frozen-response re-judging, explicit review and disagreement handling, and content-addressed evidence packages. The implemented test-family registry distinguishes executable experiments from declared but blocked capabilities. Its built-in demonstrations use deliberately synthetic cases, mock responses and mock judges, and do not establish a clinical operating point.

**Evidence:** Software-level verification is assessed through executable tests and deterministic fixtures. Separately published clinical-model evaluations motivate the measurement design and provide reusable retrospective material, but their findings were not generated as prospective validation results of this framework. The available code has not established criterion validity for detecting unsafe clinical overconfidence in an independent clinical cohort.

**Conclusions:** The framework offers a reproducible foundation for studying *defined model behaviors* under controlled evidence degradation, subject to substantial limits of clinical construct validity and judge calibration. Neither simulated demonstration success nor verification of artifact integrity should be interpreted as evidence that a model is safe for patient care.

## 1. Statement of need

Medical language models may perform strongly on static, fully specified questions yet over-commit when essential information is missing, contradictory or should trigger further information acquisition. Assessing this phenomenon requires pairing an original case with a traceable modified presentation while keeping the underlying task as comparable as possible. It also requires measurement that distinguishes identifying an unsafe assumption from becoming unhelpfully noncommittal.

LLM-as-judge systems permit larger screens but can disagree markedly with clinicians, between providers and under different grading instructions. Prior published work by the investigator identified judge-dependent apparent safety effects and associations between a judge and its own provider [1,2]. The physician-trained PrecepTron system demonstrates a different approach—task-specific calibration against physician scores—but its released rubric-specific models have not been established as detectors of the binary unsafe-overconfidence endpoint used here [3].

Clinical-AI-Eval addresses a **software and measurement-workflow gap**: a study author should be able to record what was changed in an input, which target and grader evaluated it, which inference and rubric settings were in force, and whether the resulting claim is actually supported by a verifiable run package. It does not replace clinical study design or clinical external validation.

## 2. Implementation

### 2.1 Architecture and experiment contracts

The Python package exposes explicit experiment-family definitions, target adapters, judging, analysis, human-review structures, reporting and package verification. At the time of this draft, the registry marks implemented families experimental; missing-information and conflicting-evidence are the direct reference families for paired clinical-text inputs. Patient red-flag episodes and retrieval-failure simulations are also runnable but **not clinically validated**. Citation-verification and decision-certifiability remain declared yet blocked by incomplete required components. The machine-derived capability registry, not a static paper table, is authoritative for what a given version can execute.

The software separates several stages: case construction, structural validity filtering, target-response generation, blinded evaluation, optional human review and evidence packaging. Case-specific preconstructed variants may be ingested through a family SDK. A syntactically valid variant is not thereby a clinically meaningful perturbation: the latter judgment needs qualified clinical assessment within the scope of the intended endpoint.

### 2.2 Safety, clinical utility and uncertainty are distinct

A single composite clinical safety score is avoided. The rubric distinguishes unsafe overconfidence, potentially harmful recommendations, recognition of missing/conflicting information, clinically useful next steps and excessive abstention. A model that refuses every request can avoid one class of unsafe commitment while failing clinical usefulness; those results must remain separately visible.

The framework also distinguishes blinded response grading from rubric-cued grading: an evaluator exposed to the designed perturbation may be effective at detecting the *test writer's intended defect* without measuring whether a normal blinded clinical reviewer would perceive the same failure.

### 2.3 Assessor provenance, failure handling and verifiability

Configuration and manifests identify the case pack, perturbations, subject/judge model specifications and evaluation rules. Frozen response records can be re-judged without incurring another target-generation run. The verifier checks the evidence package against recorded hashes and re-derives the applicable claim from its recorded axes. Integrity verification only checks *what an experiment reports and how its artifacts relate*; it does not independently establish that the medical labels are correct.

Human-review tools support blinded reviewer packets and adjudication, but the default framework does not possess independent physician review simply because the software can parse review files. Simulated reviewers and deliberately flawed mock subjects exercise the workflow without providing a clinical reference standard.

### 2.4 Reproducibility and software quality

The software includes a Python distribution, automated unit and integration tests, capability consistency checks, deterministic offline fixture assertions and an installable command-line interface. A fresh environment should execute the documented demonstration, inspect declared capabilities, and verify an evidence package without requiring commercial inference APIs. The pinned tested version and generated test transcript should accompany a release; numerical fixture outputs are labelled as **software checks only**.

## 3. Retrospective material and illustrative use

Two previously released investigator preprints demonstrate why the evaluation's distinct axes and judge provenance matter [1,2]. The earlier evidence-sufficiency study compared 1,200 paired model-item units across standard and evidence-sufficiency instructions and reported a judge-dependent apparent change in unsafe overconfidence. The earlier readiness study contains a blinded 50-item annotation set graded by the author and two independent clinicians, with saved provider-specific judge votes. These are **previously published datasets**, not a prospective Clinical-AI-Eval test cohort and not newly acquired independent clinical validation.

An offline read-only audit (\`tools/audit_prior_evidence.py\`) verifies label identifiers, three-rater binary annotations, judge-vote coverage, published summary arithmetic and artifact hashes from local checkouts of those studies. It neither reconstructs all unreleased original response texts nor demonstrates that the new framework would have produced identical judgments from those texts. The retrospective audit is therefore a reproducibility/provenance demonstration, not a substitute for running and calibrating the actual framework.

The software demonstration should additionally show the packaged, deterministic mock subject/test-family runs and both ordinary and over-abstaining failure modes. Because the source responses and grader behavior were constructed for the test, any numerical rates are synthetic and are intentionally excluded from clinical comparative conclusions.

## 4. Current maturity and limitations

The research implementation has not established sensitivity, specificity, predictive value or other clinical operating characteristics against a new independent physician reference standard for its main safety endpoints. No patient-outcome or deployment-safety data are included. Generated perturbations may change the medical decision task, benchmark cases may be contaminated, provider behavior may change with model version, and an automated judge may be systematically biased or over-cued. These limitations cannot be fixed by unanimous LLM votes or by choosing a physician-trained evaluator trained on a different rubric.

A one-physician review, if performed later, would yield a useful investigator-anchored *pilot* comparison but not inter-physician reproducibility or independent clinical consensus. The previously designed prospective three-physician study remains uncompleted and separate from this manuscript. The present contribution should be evaluated as **research software supporting measurement**, not as an established medical device, clinical test or standard for certifying medical model safety.

## 5. Availability and reproducibility

Source code: [KAVentures/clinical-ai-eval](https://github.com/KAVentures/clinical-ai-eval), Apache-2.0. The publication release should pin a Git commit, dependencies, compatible Python version, the exact available test families and the output of the verifier.

Example independent software checks after installation:

\`\`\`bash
python -m pip install -e ".[dev]"
python -m pytest tests_unit -q
python -m caeval.cli capabilities --check
python -m caeval.cli fixtures --check
python -m caeval.cli demo
python -m caeval.cli arms
\`\`\`

Retrospective published-result provenance (local checkouts of two separate public repositories):

\`\`\`bash
python tools/audit_prior_evidence.py \
  --readiness-root ../health-ai-readiness-robustness \
  --sufficiency-root ../clinical-evidence-sufficiency-llm \
  --out paper/prior_evidence_audit.json
\`\`\`

**Release verification to complete before submission:** run all commands on a clean setup, capture their exit codes and outputs, freeze a release SHA, validate that any stated numbers match generated artifacts, and link the exact release in the manuscript. Avoid committing HealthBench Professional case text, reviewer packets, keys or other private data.

## 6. Ethics, funding, conflicts, AI assistance

This methods/software manuscript describes software and simulations and does not collect new prospective patient or reviewer data. Its retrospective material comes from previously released studies, whose original ethics/data-use discussions remain applicable. Any later collection of novel clinical/patient information or physician review needs its own determination under relevant institutional and jurisdictional requirements.

**Draft for author confirmation:** The lead author develops the software and has an interest in its research adoption; affiliation/funding and vendor relationships should be stated accurately in the final manuscript. A single-author manuscript does not imply independent clinical validation.

**AI assistance disclosure — incomplete until human verification:** Generative AI (OpenAI GPT-6 through ChatGPT, October 2026) assisted in assembling this publication plan, writing a draft, and proposing evidence-audit code/tests. The author must inspect, edit and validate all resulting software and scientific claims and confirm the exact tool/model use before submission. This statement is not a claim that such review has already happened.

## References

1. Afrasyab K. *Judge-dependent safety gains and model-specific helpfulness costs of evidence-sufficiency prompting in clinical LLMs.* arXiv:2607.18086 (2026). https://arxiv.org/abs/2607.18086
2. Afrasyab K. *Evaluating Medical AI Under Missing Information: Same-Provider Judges and Human Raters Change Apparent Safety.* arXiv:2607.18828 (2026). https://arxiv.org/abs/2607.18828
3. Buckley TA, et al. *Scaling Clinical Judgment to Evaluate Medical AI.* arXiv:2609.12822 (2026). https://arxiv.org/abs/2609.12822

*Editorial TODO (not scientific results): verify further related frameworks and primary clinical perturbation literature; reconcile exact citation metadata; insert generated software verification table and figure; select journal-specific format only after choosing the target venue.*
