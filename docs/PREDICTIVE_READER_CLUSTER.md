# Fixed-reader, clustered clinician-versus-model evaluation

This standalone Python API extends PREDICTIVE_HUMAN_COMPARISON.md. It is not a
random-reader MRMC ROC analysis. The clinician roster is fixed; the candidate
operating point is prespecified. Repeated reader judgments on one case are
never treated as independent patient observations.

## Prospective design

Call `freeze` with the six matched comparison scope arguments and a spec:

- `cluster_by_unit`: exact mapping of every planned case to an approved
  cluster token (for example patient, not encounter when encounters repeat).
- `mode`: `descriptive` or `independent_clusters`.
- `alpha_bp`, `margin_bp`, `minimum_clusters`: null for descriptive analysis;
  otherwise one-sided alpha (1–1000 basis points), nonnegative absolute
  correctness difference margin (0–10000 basis points) and minimum number
  of clusters (at least two and no more than the frozen roster contains).

Approve and retain the analysis digest before outcome or comparator exposure.
The mapping and independence assumptions must be justified independently;
this module does not verify patient linkage, actual chronology or independence.

## Estimands and missingness

Call `analyze` with the six scope arguments, frozen design, retained analysis
 digest and the same keyword inputs as the matched comparison. It replays the
 original comparison and reference panel and validates the candidate bundle.
For each case, compute model correctness minus the average correctness of
all fixed clinicians. The reference label and model decision are shared by all
comparisons on that case, so missing values must use a common completion.
The exact numerator bounds optimize over possible model/reference decisions
and then over unknown clinician actions. Divide by the fixed reader count.
Missing cases or readers are never removed from the planned denominator.

Report both the equal-case average and equal-cluster average of case means
as exact rational bounds. The two estimands need not agree if cluster sizes
vary. These are sharp under unrestricted compatible binary completions,
not confidence intervals. The fully observed case count requires all reader
judgments, the candidate and the reference. No per-reader population claim.

## Optional conditional inference

Only in explicitly declared independent-clusters mode: each cluster mean
is bounded in [-1,1]. For K independent clusters, Hoeffding's inequality
bounds the lower tail of the unweighted mean by exp(-K*t^2/2). Subtract
sqrt(2*log(1/alpha)/K) from the sharp observed equal-cluster lower bound
and truncate at -1. This is a conservative one-sided lower bound on the
average of the cluster-mean expectations, conditional on the fixed reader
roster and independently justified cluster sampling assumptions. It allows
arbitrary dependence within clusters; it does not address dependence across
clusters, reader sampling, selection, outcome misclassification or external
validity. Missingness is handled by worst-case completion, not imputation.
The margin signal is strictly lower bound > negative prespecified margin.
It is exploratory and not a clinical noninferiority conclusion. Arithmetic
for the confidence radius is floating point; exact rational descriptive
bounds remain separate. No multiplicity adjustment, sample size guarantee,
random-reader inference or clinical deployment approval is supplied.

`verify` recomputes the full report from retained inputs. Store all evidence
and checkpoints privately. No browser, CLI or clinical validation is provided.

    python -m pytest -q tests_unit/test_predictive_reader_cluster.py
