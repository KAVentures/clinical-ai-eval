# Longitudinal institutional evidence review

The unified programme handoff captures a single administrative observation.
Use the comparison tool to prepare a follow-up committee review of **the same
frozen programme**. It does not run clinical tests, re-open a frozen plan,
or establish a clinical finding. It requires no network, external model, or
hospital workstation installation: an authorised administrator can export the
two handoffs in the hospital browser and an approved server operator can run
the offline tool.

## Collect and compare

1. In the institutional unified gap view, preview and explicitly approve the
   baseline export. Record its displayed SHA-256 digest independently of the
   downloaded file using the organisation's approved register.
2. At the next review, repeat for the same programme. Record the follow-up
   digest independently. The two exports remain private administrative evidence.
3. On the approved server, using the installed package or source checkout:

```sh
python -m caeval.unified_gap_comparison compare baseline.json followup.json \
  --baseline-digest "$BASELINE_SHA" --followup-digest "$FOLLOWUP_SHA" > comparison.json
```

The report includes its own digest. Store the comparison digest and original
handoffs separately in the committee evidence register. Replay with all three
independently obtained digests:

```sh
python -m caeval.unified_gap_comparison verify baseline.json followup.json \
  --baseline-digest "$BASELINE_SHA" --followup-digest "$FOLLOWUP_SHA" \
  --comparison comparison.json --expected-digest "$COMPARISON_SHA"
```

The commands emit JSON to stdout on success and refuse invalid, oversized,
duplicate-key or incompatible inputs with a nonzero exit. Do not consume output
after an unsuccessful invocation. Use a private destination under the approved
retention policy. No source text, case responses, native work identifiers or
reviewer identities are added by the comparison.

## Interpretation

- Exact programme definition, plan versions, vendor and requirement scopes
  must match. A changed programme is not comparable in this tool: use the
  governed version reassessment path instead.
- Every requirement and every required canonical suite remains visible,
  including unplanned or unsupported suites. No total score or vendor ranking.
- Newly projected, no longer projected and persistent tasks are distinct.
  Disappearance from a projection does not prove actual task completion.
- Lost availability, source association metadata changes, stale plans and
  incomplete scans are explicitly flagged for administrative follow-up.
- Work count deltas are reported only when both snapshots have current plans
  and nontruncated bounded scans. Even then they are bounded observations,
  not a census or proof of job transitions. Other scans show observations
  but a null delta.
- Source metadata digests let the committee identify a change without copying
  the source identifiers into the comparison. The original exports must be
  retained for independent replay.
- Identical exports are explicitly labelled; the caller determines order.
  No trustworthy timestamp, independent source record attestation, signature,
  clinical validation, decision grade or deployment authority is established.
- The tool uses the actual native handoff verifier before comparing and
  reconstructs the entire output when replaying. Digests protect only when
  obtained through a trusted channel: computing a new digest from an untrusted
  file does not authenticate it.

The Python entry point is available in an installed wheel through the same
package discovery used by the rest of caeval; it is intentionally separate
from the main CLI parser to preserve the existing stacked CLI changes.
