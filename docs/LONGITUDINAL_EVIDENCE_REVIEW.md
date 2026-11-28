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

## Administrator browser workflow (same institutional workspace)

1. Open **Unified assessment → Evidence gaps → Preview portable evidence-gap
   handoff**. Approve and download the baseline. Record the displayed digest
   independently in the approved evidence register.
2. At a later review, open **Compare with previous approved handoff** from
   the same programme. Paste the baseline JSON and independently recorded
   digest. The browser import limit is 1 MiB (UTF-8 and canonical serialized
   baseline); use the offline comparison CLI for larger handoffs.
3. Review every required suite and requirement, flags, bounded work counts,
   and new, persistent and no-longer-projected tasks. Record the baseline,
   follow-up and exact review bundle digests independently.
4. Confirm download of the exact private review bundle. If evidence changes
   after preview, download is refused: preview again.

No new server-side historical snapshot store is introduced. The browser uses
the existing institutional account and origin/CSRF boundaries; only an active
administrator can compare or download. A baseline must match the frozen local
programme, pass native structural replay and have an existing successful local
handoff-export audit event for that exact digest. A prior audit event is a
local record, not an externally authenticated signature. The follow-up and
bundle export events are committed together; the follow-up can be used as the
next baseline. An identical handoff is explicitly shown as identical, not as
a verified elapsed period. A failed export does not append successful export
events. The private bundle contains both handoffs and the derived comparison;
it adds no clinical raw responses or unassociated native job IDs.

Replay the complete bundle on an approved server with no browser or network:

```sh
python -m caeval.institution_gap_review verify unified-gap-review.json \
  --baseline-digest "$BASELINE_SHA" --followup-digest "$FOLLOWUP_SHA" \
  --expected-digest "$BUNDLE_SHA"
```

Use independently recorded digests. Offline replay cannot verify the original
local audit database, the actual chronology, or the source records. Store the
bundle and its digests according to the hospital's existing governance and
retention policy. There is no automatic committee disposition, clinical claim,
or reassessment execution; changes in frozen programme scope must use the
existing governed reassessment workflow.
