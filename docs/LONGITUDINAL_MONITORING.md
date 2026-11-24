# Longitudinal regression surveillance (experimental)

This is repeated **fixed-case regression testing**, not real-time patient
surveillance or a determination of clinical safety. Use approved synthetic
or non-identifiable cases and a controlled local candidate endpoint. The
institution must independently approve the data, endpoint, judge configuration,
worker environment and clinical response to findings.

## Freeze a schedule

In the institutional browser, an administrator opens **Longitudinal regression
monitoring**. Supply an open lifecycle pilot, a verified completed baseline
job, an approved single-turn response exchange, local connector, judge panel,
governance answers, cadence, UTC start time and an absolute descriptive
alert threshold (e.g. 0.10 means 10 percentage points).

The baseline and new exchange must have identical vendor, product name, case
pack, prompt slots, families, scientific definitions, intended use, judge panel
and governance. A deliberate new product version is permitted only if it
matches the exact version of the linked pilot. Connector configuration is
also frozen. Changed scientific conditions require a new verified baseline
rather than a misleading comparison. Mock-panel work remains demonstration
evidence. The software cannot independently establish that an endpoint is
actually running the declared product version or that judge deployments
have not changed behind the configured model identifiers.

Only administrators can freeze and disable schedules. Operators and observers
can inspect monitoring results. Reviewers cannot access the register.
A browser action never makes candidate or judge calls.

## Approved worker

Use a trusted server process supervisor and OS scheduler (for example, a
reviewed systemd timer) to periodically invoke:

    python -m caeval.institution_monitoring_cli /srv/caeval/workspace \
      --schedule MONITOR_ID --approve-binding EXACT_BINDING_HASH \
      --allow-candidate-calls

If real judges are configured, an independently authorized worker must also
include --allow-model-calls. This flag authorizes the existing configured judge
transport; it does not itself restrict judge network egress. Apply institutional
egress policy and resource limits to the worker. The browser and worker have
separate execution responsibilities.

One invocation claims at most one due occurrence. There is no catch-up burst:
missed intervals are counted and the most recent due occurrence is attempted.
A transactional claim prevents duplicate work on the same occurrence.
Each occurrence collects fresh candidate responses, prepares a new bound
assessment, invokes the canonical evaluation runner, and verifies the resulting
packages and collection receipt. The worker never automatically retries failed
calls or incomplete occurrences. Partial collection does not enter scoring.

Disabling a schedule blocks subsequent claims and is checked between phases;
it cannot retract an already issued request or instantly stop a running child
process. If the worker is interrupted, investigate the durable running
occurrence and its recorded child identifiers. Do not manually reset running
records or start another occurrence to conceal a failed run. Database and
filesystem operations are not one transaction; crashes between child creation
and checkpoint publication may require additional reconciliation.

## Interpret the results

The dashboard compares the same structurally eligible variant cells using
the existing blinded panel-any-unsafe, panel-majority-unsafe and potentially
harmful treatment fields. It shows matched observed/eligible denominators,
baseline and retest rates among matched observations, missing pairs and
directional label changes. A delta and descriptive increase alert require
complete matched coverage and at least one eligible cell. Incomplete coverage
never produces a passing result. An alert is a prompt for investigation; no
alert is not evidence of equivalence, absence of harm or approval.

These are descriptive repeated fixed-pack measurements, not independent
population estimates or multiplicity-adjusted statistical tests. They may
reflect judge variability, stochastic product behavior or unobserved changes
in deployed systems. The original scientific scoring and adjudication gates
are unchanged. Required but unavailable families remain in the report.
Clinical reviewers and the institution determine whether further investigation,
new clinical evidence, incident escalation or pausing a pilot is appropriate.
No automated clinical action or lifecycle deployment approval is issued.

The original assessment, verified collection receipt and retest package hashes
remain linked. A report whose digest or underlying packages fail verification
is shown as invalid. Preserve the access database, child job records and
scientific artifacts under approved retention and recovery procedures. Stop
the scheduler and all workers before backup/restore and investigate any
running occurrences. Actual deployment acceptance remains mandatory.
