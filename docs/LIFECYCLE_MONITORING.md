# Institutional clinical AI lifecycle monitoring

This workflow records operational monitoring of externally authorized clinical
AI use. It is not a clinical deployment authorization or independently
validated safety surveillance system. The application never sends alerts to
external systems, stops a product, changes clinical care, or rolls back a
deployment.

## Registration

An administrator selects an existing current frozen buyer programme and its
exact vendor and declared product version. Register one monitor for that
vendor/programme with the intended-use identifier, the SHA-256 of an external
authorization record, and an explicit external-authorization attestation.
The application does not read or authenticate the authorization artifact.
Registration can describe already existing operations; it is not proof that
thresholds were prospectively defined before data collection.

The institution chooses metric definitions and thresholds outside this
application. Definitions should specify target population, inclusion and
exclusion criteria, outcome attribution, denominator and data extraction.
The digest binds a declared definition, not verified semantics or data quality.

Example (fictional; thresholds are not clinical recommendations):

```json
{
  "monitor_id": "fictional-monitor",
  "programme_id": "existing-programme",
  "vendor_id": "vendor-a",
  "intended_use_id": "fictional-use",
  "start_date": "2026-01-01",
  "window_days": 7,
  "windows": 12,
  "authorization_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "external_authorization_attested": true,
  "metrics": [
    {
      "id": "fictional-adverse-rate",
      "direction": "higher_is_worse",
      "threshold_bp": 1000,
      "min_denominator": 10,
      "definition_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    }
  ]
}
```

Preview and explicitly approve the plan. Bounds: 12 metrics, 104 fixed
windows, 1–31 UTC days per window, 60 institutional monitors. Windows use
inclusive start and exclusive end dates; there is no configurable grace
period. The server UTC date determines when a window is due. A finite
schedule has an explicit end; no implied continuing surveillance follows.
No automated collection, scheduler, push notification or external delivery
is included. A responsible operator must check and submit records.

## Aggregate reports

Operators and administrators submit one immutable complete count inventory
for each completed window. Each row carries an integer numerator and
denominator, with numerator no greater than denominator; the maximum count
is ten million. The exact plan digest, candidate identity and source digest
are required, along with an aggregate completeness attestation. Do not use
patient identifiers in any token, and do not submit individual records.

Late reports can fill missing windows, but cannot replace a previously
locked report. Missingness, insufficient volume and threshold breaches
are displayed separately. Thresholds are inclusive and evaluated by integer
cross multiplication of rates and basis points. A denominator below its
prespecified minimum yields insufficient volume, not a favorable rate.
A null denominator yields null rate. No statistical significance, confidence
interval, case-mix adjustment, drift inference, or validated clinical safety
threshold is implied. Changes in the source population or definitions must
be addressed by a separate monitoring design.

## Incidents and escalation

Submit bounded structured incident metadata (no narrative or case details):
opaque incident ID, UTC date, category, severity, external record digest
and observed product version. Clinical safety, data quality, privacy/security,
workflow and availability incidents can be recorded. An explicitly reported
different version produces a critical version-change alert and an enduring
reassessment requirement. Reports overlapping that change are refused after
the change is known. Already locked overlapping reports remain visible and
are explicitly flagged. They must not be interpreted as clean version-specific
evidence. A new version requires a separate buyer programme and monitor.
The application cannot independently discover a deployed product version.

Administrators can record acknowledgment, investigation, reassessment request,
pause request, rollback request or external resolution documentation against
an existing alert. Each action requires an external record digest. These
are documentary records only. Acknowledgment is not resolution. Recording
external resolution changes the attention flag for an ordinary incident,
not the truth of external resolution; threshold, version and missingness
conditions cannot be cleared by this action. A late report removes the
corresponding active missing-window condition while its prior actions remain
in the historical ledger.

## Governance and replay

Operators see the detailed monitoring state; observers see restricted
coverage. Administrators have access to the underlying records and can
approve a restricted export. Buyer-scope drift is flagged in live coverage,
but does not prevent reporting incidents about a historically deployed
product. The institutional committee includes date-specific monitoring
coverage; a snapshot can become stale when the UTC date or records change.

The local audit ledger checks inventory, immutable record digests, and
the ordering of actions against alerts known at recorded event time.
It does not protect against a privileged attacker rewriting the entire
database and audit history. Offline export replays the structural counts,
alerts and recorded actions at its saved as-of date; it does not independently
verify audit chronology, external records, source data, or authorization.

```sh
clinical-ai-eval verify-lifecycle-monitor evidence.json --expected-digest TRUSTED_DIGEST
```

Keep exports, digests and external artifacts in the approved institutional
environment. No result of this workflow authorizes use or establishes safety.

## Optional collector integration

The [scoped local ingestion API](MONITOR_INGESTION.md) accepts authorized aggregate report/incident batches and exposes a minimal alert feed for institution-managed polling. No scheduler or external delivery is installed by the application.
