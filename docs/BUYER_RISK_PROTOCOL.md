# Buyer clinical risk protocols

In Assessment setup, open **Buyer clinical risk protocol** for a saved evaluation plan.
The form uses the existing scientific selection rules to identify all required
suites and provides qualitative hazard prompts. It never preselects a numerical
safety criterion. Enter the intended harm, severity, endpoint, eligible denominator,
missingness handling, acceptance proportion, rationale and mitigation for each
selected suite. Record clinical population, setting limitations, human oversight,
data governance, jurisdiction-specific review and stop rules.

The browser currently accepts one hazard per selected suite; the underlying
Python API supports up to 40 distinct hazards. Unselected suites remain explicit
coverage gaps. A suite that cannot run canonically remains blocked even when a
hazard is authored. Thresholds are buyer planning inputs only: this workflow
does not activate the canonical comparator, assert the endpoint is measurable,
or establish prospective preregistration. A trusted operator must separately
freeze scientifically appropriate canonical conditions before candidate testing.

The exact plan, version, definitions, governance and hazards are bound into an
immutable digest with an audit event. Revisions require a distinct saved plan;
previous records remain historical. If the buyer scope or scientific definitions
change, currentness is lost rather than silently reinterpreting the record.
Administrators and operators can read and download the full plan. The committee
snapshot includes only restricted metadata: hazard counts, critical counts,
scope status, uncovered suites and blocked suites. Private rationales and
threshold values remain in the planning view. A changed plan inventory changes
the committee snapshot. All outputs remain experimental and non-decision-grade.

Run the synthetic browser and ledger tests with:

    python -m unittest tests_unit.test_buyer_risk_protocol

Real thresholds, measurement validation, local regulatory obligations and
clinical governance must be determined by the healthcare organisation.
