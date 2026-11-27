# Activate buyer risk criteria in canonical assessments

This optional workflow starts from a current frozen buyer risk protocol. It
supports the existing single-turn clinician paired-text engine only. Choose
a staged case pack and judge panel and explicitly map hazards to top-level
canonical binary metrics: panel_any_unsafe, panel_majority_unsafe, or
potentially_harmful_treatment. A criterion applies to all generated cells
(originals and variants); incomplete quorum and invalid variants remain
missing. This deliberately does not interpret the free-text endpoint or
denominator as a machine-readable clinical rule. The buyer must explicitly
approve the actual mapping and denominator.

Every runnable family must have a mapped hazard. Unmapped hazards and required
but not runnable suites remain listed in the activation receipt. Administrators
approve the exact preview digest before activating. The existing procurement
state is initialized using the compiled criteria and the exact candidate
identity. Existing studies are never replaced. The activation can optionally freeze multiple distinct candidates with identical
clinical scope and generated prompts. See [shared risk conditions and handoff](RISK_MULTI_CANDIDATE.md).

The conditions hash is included in a prepared canonical project only by
explicit opt-in. The active study page accepts a complete collected submission
under the same plan, pack and panel. Existing job preparation, queue, trusted
worker execution and review workflows remain available. No model call occurs
on activation or preparation. The existing CLI assessment preparation JSON
accepts an optional conditions_hash for the same path. Legacy preparation
without that field remains unchanged.

Package ingestion additionally checks the candidate/version, selected family,
family and selection definitions, recorded judge-list and prompt hashes, pack
and exact expected result-cell inventory. Caller-supplied fixture results are
not accepted for activated studies. Invalid or incomplete result metrics are
converted to missing in the comparator's copy, not in the original evidence.
The staged panel file is byte-checked during bound preparation; package
verification binds the recorded judge list, not every possible execution
environment setting. Existing institutional committee import staging rules
still apply. No new scientific authority, clinical validation, deployment
permission or comparative superiority is inferred.

Run the synthetic acceptance tests:

    python -m unittest tests_unit.test_buyer_risk_activation

This is software acceptance, not clinical validation. Independently verified evaluator runtime settings and additional executor
support remain separate follow-up work. Governed job handoff and multi-candidate
activation are described in the linked guide.
