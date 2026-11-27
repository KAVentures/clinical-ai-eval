# Institutional multi-module research programmes

From Assessment setup, open a saved buyer plan's related experimental research
coverage, then **Coordinate a frozen multi-module assessment programme**.
This is the browser equivalent of coordinating a scoped set of experimental
modules, using the existing institutional research studio rather than requiring
a local CLI on each workstation.

1. In the research studio, freeze one native specification for each relevant
   module you intend to assess. Do not export candidate-facing materials yet.
2. On the programme page select the matching planned studies owned by your
   account and explicitly approve the scope. Each study may join only one
   programme. Existing matching planned buyer associations are reused;
   missing ones are created in the same SQLite transaction as the programme.
3. Follow the existing study collection and physician-review workflow. Review
   rosters remain governed independently through the prospective protocol and
   canonical module checks. Freezing the programme does not queue a job or
   authorize a model call.
4. Open the programme page to inspect each module state, review protocol status,
   verified report link and all required canonical suites. Unplanned modules
   and incomplete or unavailable studies remain visible.
5. An administrator may explicitly approve **Replay and download summary
   dossier**. Each available scientific report is independently regenerated
   from its governed inputs and normalized review submissions using the existing
   replay implementation. The summary JSON records report and replay-package
   digests, per-module descriptive summaries, coverage and explicit gaps.
   No raw clinical inputs, individual ratings or original signing secrets
   are deliberately included in this summary. Nevertheless aggregate results
   may be confidential. Share only through approved institutional channels.

The summary is a snapshot, not a self-contained independently replayable
bundle. Use the individual administrator evidence export for the complete
replayable material. Digest binding is not a signature. Failed or oversized
replays are disclosed as unavailable, not counted as successes. A programme
can have all *planned* reports replayed while still omitting other relevant
modules. No overall score or procurement recommendation is produced.

## Access and scope

Operators see only their own programmes. Administrators can inspect and
export all programmes; to freeze a new programme, administrators and operators
must use studies owned by their own account. Reviewers still receive only
their own existing assigned packets. The study owner, module, exact target
version, buyer scope, scientific plan and association digests are checked
again on every programme read. Existing account, origin and CSRF checks apply.
Local audit sequence prevents registration after a recorded candidate exchange
or collection; this is not external proof of preregistration or blinding.

The programme is a buyer-preview coordination layer, not a validated
canonical run project, formal committee decision, or a clinical deployment
gate. It does not change canonical family availability or clinical maturity.
The separate full committee dossier integration remains separate. A programme
cannot be edited after freeze; create fresh studies and a new programme when
scope changes. The data reside in the existing institutional access database
and study directory, with the existing stopped-service backup boundary.

Run the synthetic browser/ledger acceptance suite:
    python -m unittest tests_unit.test_research_program

## Committee evidence snapshots

The procurement committee now includes aggregate programme coverage in its lifecycle inventory. All committee roles see the same restricted projection: vendor/version, frozen programme digest, module states, available report digests and canonical gaps. Owners, physician identities, responses, report summaries and protocol text are excluded. A changed programme or verified report invalidates the current status of an older committee snapshot. No clinical decision or procurement authority is inferred. Full replay remains a distinct administrator-authorized export.
