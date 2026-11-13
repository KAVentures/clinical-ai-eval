# Hidden-Fact Impossibility for Clinical AI Safety Evaluation

## A fiber criterion, a minimax lower bound, and NP-completeness of critical-question closure

**Status:** complete mathematical result with finite executable witnesses; not peer reviewed; novelty not independently certified.

## Abstract

Clinical-AI evaluators are increasingly asked to label a free-text answer as safe or unsafe from an exposed vignette and the answer itself. This paper resolves a precise limitation of that setup. Let a *complete clinical world* contain all patient and setting facts, let the *observation map* hide some of those facts, and let the safety label of a proposed action depend on the complete world. We prove that a perfect evaluator using only the exposed case and response exists **if and only if** the safety label is constant on every fiber of the observation map. Consequently, whenever two clinically admissible worlds produce the same evaluator input but make the same proposed action safe in one world and unsafe in the other, no deterministic judge, LLM jury, voting rule, or randomized evaluator can be both sound and complete. Any randomized evaluator has worst-case error at least one half on that witness pair.

We then characterize exactly what extra information repairs the impossibility. A set of clinical questions is sufficient if and only if it separates every same-observation pair of worlds with opposite safety labels. We call this **critical-question closure**. The smallest such question set is a hitting-set/test-cover problem. We give a direct reduction from Hitting Set, proving that the finite decision version is NP-complete. The result converts a common evaluation caveat—“important context may be missing”—into an exact theorem and a machine-checkable design rule: an automated safety verdict is logically certifiable only after the evaluator input closes all action-critical indistinguishability classes, or else the system must defer.

The repository package contains a dependency-free implementation, unit tests, a synthetic clinical witness, an exact closure solver for small instances, and a replay script that verifies every finite claim.

---

## 1. The methodological question resolved

The question is not whether a particular LLM judge is accurate on average. It is more basic:

> **Can any evaluator determine whether a proposed clinical action is unsafe when the evaluator sees only an incomplete case and the response, while a hidden fact can change the correct safety label?**

The answer is exact:

> **Only when safety is already a function of the evaluator's inputs.**

More judges can reduce sampling noise or idiosyncratic bias. They cannot recover information absent from every judge's input.

This matters for missing-information tests, conflicting-evidence tests, response-only grading, and any proposed “decision-certifiability” layer. A rubric, a larger panel, or a stronger model is not a substitute for action-critical evidence.

---

## 2. Formal model

Let:

- \(W\) be a nonempty set of complete clinical worlds.
- \(X\) be the set of observed cases exposed to the evaluated system and evaluator.
- \(O:W\to X\) be the observation map.
- \(R\) be the set of possible responses.
- \(A:R\to\mathcal A\) extract the proposed clinical action from a response.
- \(H:W\times\mathcal A\to\{0,1\}\) be the ground-truth unsafe-action predicate, with \(1\) meaning unsafe.

An evaluator that sees only the observed case and response is a function

\[
E:X\times R\to\{0,1\}.
\]

It is **perfect** on the admissible world class when

\[
E(O(w),r)=H(w,A(r))
\quad\text{for every }w\in W\text{ and }r\in R.
\]

For an observation \(x\in X\), its fiber is

\[
O^{-1}(x)=\{w\in W:O(w)=x\}.
\]

The fiber represents all complete worlds that are indistinguishable to an evaluator given only \(x\).

---

## 3. Main theorem: the clinical safety fiber criterion

### Theorem 1 — Hidden-fact identifiability

A perfect evaluator \(E:X\times R\to\{0,1\}\) exists **if and only if**, for every response \(r\in R\), the function

\[
w\mapsto H(w,A(r))
\]

is constant on every fiber of \(O\).

Equivalently, for every \(w,w'\in W\) and \(r\in R\),

\[
O(w)=O(w')\implies H(w,A(r))=H(w',A(r)).
\]

### Proof

**Necessity.** Assume a perfect evaluator exists. Take any worlds \(w,w'\) with \(O(w)=O(w')\), and any response \(r\). Because the evaluator receives exactly the same input pair in both worlds,

\[
E(O(w),r)=E(O(w'),r).
\]

Perfectness gives

\[
H(w,A(r))=E(O(w),r)=E(O(w'),r)=H(w',A(r)).
\]

Thus the safety label is constant on every observation fiber.

**Sufficiency.** Assume the label is constant on every fiber for every response. For any observed case \(x\) in the image of \(O\), choose any \(w\in O^{-1}(x)\) and define

\[
E(x,r)=H(w,A(r)).
\]

This is well defined because the assumed fiber constancy makes the value independent of which \(w\in O^{-1}(x)\) is selected. Define \(E\) arbitrarily for observations outside the image of \(O\). Then for every \(w\in W\) and \(r\in R\),

\[
E(O(w),r)=H(w,A(r)).
\]

So \(E\) is perfect. ∎

### Corollary 1 — Explicit counterexample criterion

If there exist \(w_0,w_1\in W\) and a response \(r\) such that

\[
O(w_0)=O(w_1)
\quad\text{but}\quad
H(w_0,A(r))\ne H(w_1,A(r)),
\]

then no evaluator seeing only \((O(w),r)\) can be both sound and complete.

This pair is an **indistinguishable-world witness**.

### Corollary 2 — An LLM jury cannot repair missing information

Any finite or countable panel of judges, together with any deterministic aggregation rule, is itself a function of the panel's common inputs and outputs. If all panel members receive no information that distinguishes \(w_0\) from \(w_1\), the aggregate verdict receives none either. Therefore the impossibility applies unchanged to majority vote, unanimity, panel-any/fail-closed vote, weighted vote, debate, and judge-of-judges architectures.

The result does **not** say panels are useless. Panels can reduce variance and expose evaluator disagreement. It says they cannot make a non-identifiable label identifiable.

---

## 4. Randomized evaluators: a sharp minimax bound

A randomized evaluator presented with the shared input can output “unsafe” with some probability \(q\in[0,1]\).

### Theorem 2 — Randomized lower bound

For an indistinguishable opposite-label pair, every randomized evaluator has worst-case error at least \(1/2\). The bound is sharp.

### Proof

Suppose \(H(w_0,A(r))=0\) and \(H(w_1,A(r))=1\). Since the evaluator sees the same input in both worlds, it uses the same output distribution. If it predicts unsafe with probability \(q\), its error is \(q\) in \(w_0\) and \(1-q\) in \(w_1\). Therefore

\[
\max\{q,1-q\}\ge \frac12.
\]

Equality is achieved by \(q=1/2\). ∎

### Corollary 3 — Bayes error

If the unsafe member of the indistinguishable pair has conditional prior probability \(p\), the minimum Bayes error is

\[
\min\{p,1-p\}.
\]

Randomization cannot beat the best constant prediction under that prior.

---

## 5. What additional information is sufficient?

Let \(Q\) be a finite collection of candidate clinical questions. Each question \(q\in Q\) has an answer function

\[
q:W\to V_q.
\]

For a selected set \(T\subseteq Q\), let \(q_T(w)\) denote the vector of answers to all questions in \(T\).

For a fixed response \(r\), call a pair \((w,w')\) **dangerous** when

\[
O(w)=O(w')
\quad\text{and}\quad
H(w,A(r))\ne H(w',A(r)).
\]

A question \(q\) **separates** that pair when \(q(w)\ne q(w')\).

### Definition — Critical-question closure

A set \(T\subseteq Q\) is a critical-question closure for the action induced by \(r\) when the augmented input

\[
(O(w),q_T(w),r)
\]

makes the unsafe label identifiable.

### Theorem 3 — Dangerous-pair characterization

A question set \(T\) is a critical-question closure **if and only if** every dangerous pair is separated by at least one question in \(T\).

### Proof

**Only if.** Suppose a dangerous pair \((w,w')\) is not separated by any question in \(T\). Then

\[
O(w)=O(w')\quad\text{and}\quad q_T(w)=q_T(w'),
\]

while the safety labels differ. The augmented evaluator input is still identical in the two worlds, so Theorem 1 rules out perfect evaluation.

**If.** Suppose every dangerous pair is separated by some question in \(T\). Consider any two worlds with the same augmented input \((O(w),q_T(w))\). They cannot form a dangerous pair, because a dangerous pair would have been separated. Hence their safety labels agree. The label is therefore constant on each augmented observation fiber, and Theorem 1 supplies a perfect evaluator. ∎

This is the exact condition that a clinical rule bundle or clinician-authored question set must satisfy. Merely listing plausible questions is insufficient; the selected questions must close every opposite-label indistinguishability class admitted by the world model.

---

## 6. Computational result: minimum closure is NP-complete

Define the finite decision problem **CLINICAL-SAFETY-CLOSURE**:

**Input:**

1. a finite set of worlds \(W\);
2. an observation value \(O(w)\) for each world;
3. a binary unsafe label \(H(w,a)\) for one fixed action \(a\);
4. a finite query-answer table \(q(w)\);
5. an integer \(k\).

**Question:** Does there exist a set of at most \(k\) queries that separates every dangerous pair?

### Theorem 4 — NP-completeness

CLINICAL-SAFETY-CLOSURE is NP-complete.

### Proof

**Membership in NP.** A certificate is a set \(T\) of at most \(k\) queries. Enumerate all pairs of worlds with equal observation and opposite labels. For each pair, check whether at least one query in \(T\) gives different answers. This takes polynomial time in the explicit input size.

**NP-hardness.** Reduce from Hitting Set. Let a Hitting Set instance have universe

\[
U=\{e_1,\dots,e_m\},
\]

subsets \(S_1,\dots,S_n\subseteq U\), and budget \(k\).

For every element \(e\in U\), construct two worlds \(w_e^0,w_e^1\) with a unique shared observation \(x_e\). Give them opposite labels:

\[
H(w_e^0,a)=0,
\qquad
H(w_e^1,a)=1.
\]

For every subset \(S_j\), create a query \(q_j\). Set

\[
q_j(w_e^0)=0
\]

for every \(e\), and

\[
q_j(w_e^1)=
\begin{cases}
1,&e\in S_j,\\
0,&e\notin S_j.
\end{cases}
\]

The only dangerous pair in observation fiber \(x_e\) is \((w_e^0,w_e^1)\). Query \(q_j\) separates this pair exactly when \(e\in S_j\). Therefore a query set \(T\) closes every fiber exactly when the corresponding subsets hit every element of \(U\). The query budget equals the hitting-set budget. The construction is polynomial. Hence CLINICAL-SAFETY-CLOSURE is NP-hard, and therefore NP-complete. ∎

### Interpretation

The intractability result is not an excuse for vague evaluation. It specifies the engineering boundary:

- exact search is appropriate for small, high-severity rule bundles;
- greedy or other approximation methods may be needed for large candidate-question sets;
- every approximate result must be labeled as such;
- a failure to find a small closure is not proof that no small closure exists unless the search is exact or certified.

---

## 7. Executable clinical witness

The reproduction package contains a deliberately generic, synthetic witness rather than patient advice.

The observed case says only that an adult has a condition for which action X may be considered; contraindication status is omitted. Two complete worlds are admitted:

| world | exposed case | hidden contraindication | unsafe label for “start action X” |
|---|---|---:|---:|
| \(w_0\) | identical | absent | 0 |
| \(w_1\) | identical | present | 1 |

The same response proposes action X in both worlds. Any evaluator that sees only the exposed case and response must emit the same verdict for both. Label 0 misclassifies \(w_1\); label 1 misclassifies \(w_0\). Asking `contraindication_status` separates the pair and restores exact certification. Asking an irrelevant question whose answer is identical in both worlds does not.

The witness is intentionally abstract. The theorem does not depend on any particular drug, diagnosis, or guideline. In a real clinical application, the admissible worlds and unsafe labels must be constructed from a version-pinned rule bundle and independently reviewed clinical judgments.

---

## 8. Consequences for clinical-AI evaluation

### 8.1 Response-only safety grading has a hard validity boundary

A response-only or vignette-only judge can validly grade only properties determined by its inputs. When the correct label depends on a hidden fact, the problem is not merely that a judge may reason poorly. The target label is not identifiable.

### 8.2 Rubric-aware judging does not necessarily solve the problem

Giving a judge the name of an omitted field can help only if the field's value or a sufficient rule about it is provided. “Renal function is missing” and “renal function equals value z” are different information states. Metadata that a perturbation occurred can cue defect detection while leaving the action's true safety unresolved.

### 8.3 Human review is necessary but not magically sufficient

A clinician who sees the same incomplete inputs is subject to the same theorem. Expertise cannot distinguish worlds that the review packet leaves observationally identical. Human adjudication becomes decisive only when reviewers receive, establish, or explicitly model the action-critical facts and admissible-world assumptions.

### 8.4 Judge panels should report non-identifiability separately from disagreement

Inter-judge disagreement is empirical variation. Non-identifiability is structural. A unanimous panel can still be wrong because all judges share the same missing information. Evaluation reports should therefore separate:

1. **identified safe/unsafe:** the label is constant after closure;
2. **defer/non-identifiable:** compatible worlds retain opposite labels;
3. **evaluator disagreement:** judges differ despite an identifiable target;
4. **invalid world model:** the admissible worlds or labels lack clinical validation.

### 8.5 The result justifies a fail-closed certifiability gate

Before a decision-certifiability family emits a clinical safety verdict, it should require:

- an extracted action;
- a version-pinned rule bundle or clinician-adjudicated world model;
- an explicit list of candidate critical questions;
- a closure certificate showing every dangerous pair is separated;
- a `DEFER` result when closure is absent or answers remain unknown.

The theorem shows that `critical_question_closure` is not optional metadata. It is logically necessary.

---

## 9. Relationship to prior work

This result is narrower and more input-identifiability-focused than general black-box safety lower bounds. General work has shown that deployment risk can be impossible to estimate under latent triggers and distribution shift. Clinical studies have also shown that LLM judges and clinician evaluators disagree, that model juries do not consistently remove bias, and that judge identity can change apparent model rankings. Those findings motivate the problem but do not by themselves provide the exact fiber criterion or the dangerous-pair closure theorem stated here.

The minimum closure problem is deliberately identified with classical Hitting Set / Minimum Test Set / Test Cover structure. The claim is **not** that NP-hardness of hitting set is new. The contribution is the exact reduction and interpretation for clinical-AI safety-label certifiability.

A targeted literature search performed on 3 August 2026 did not locate this exact healthcare-specific theorem under the names “critical-question closure,” “clinical safety fiber criterion,” or “response-only clinical safety identifiability.” That is evidence of possible novelty, not proof of novelty. Independent literature review and peer review are still required.

Selected nearby literature:

1. Williams G, Rutunda S, Nzabakira F, et al. *Human evaluators vs. LLM-as-a-Judge: toward scalable evaluation of GenAI in global health.* npj Digital Medicine. 2026. doi:10.1038/s41746-026-02992-w.
2. Afrasyab K. *Evaluating medical AI under missing information: same-provider judges and human raters change apparent safety.* arXiv:2607.18828. 2026.
3. Srivastava V. *Fundamental Limits of Black-Box Safety Evaluation: Information-Theoretic and Computational Barriers from Latent Context Conditioning.* arXiv:2602.16984. 2026.
4. Kocaman V, Talby D, et al. *Clinical Large Language Model Evaluation by Expert Review (CLEVER): Framework Development and Validation.* Journal of Medical Internet Research. 2025. doi:10.2196/72153.
5. Chen GH, Chen S, Liu Z, Jiang F, Wang B. *Humans or LLMs as the Judge? A Study on Judgement Bias.* EMNLP 2024.

---

## 10. Reproducibility

From the repository root:

```bash
python -m unittest tests_unit.test_identifiability
python research/clinical_safety_nonidentifiability/reproduce.py --check
```

The replay script verifies:

- the explicit indistinguishable-world witness;
- failure of both possible deterministic labels;
- the randomized minimax lower bound of 0.5;
- failure of an irrelevant question to close the fiber;
- success of the action-critical question;
- `DEFER` before the answer and exact certification after it;
- preservation of the optimum in a finite Hitting Set reduction.

The script records SHA-256 hashes of the witness fixture and implementation in its generated report. `EXPECTED_RESULT.json` excludes those hashes and checks the semantic result, so harmless file-location changes do not create false drift.

---

## 11. Claim boundary and limitations

This package proves a mathematical statement about an explicitly defined model. It does **not** establish that any particular clinical AI product is safe or unsafe. It does not validate the supplied synthetic labels clinically. It does not solve action extraction, provenance verification, rule-authoring, world-model completeness, or post-deployment monitoring.

The theorem is exact conditional on the admissible-world class. If the world model omits a clinically possible dangerous state, the closure certificate can be falsely reassuring. Thus the strongest practical claim is:

> Given a clinically valid world model, action labels, and query-answer semantics, critical-question closure is necessary and sufficient for exact safety-label identifiability from the augmented evaluator input.

That conditional form is the appropriate publication claim pending independent clinical and mathematical review.
