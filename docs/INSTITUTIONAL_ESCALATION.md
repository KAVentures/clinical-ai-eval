# Institutional escalation inbox

The institutional browser includes a local, on-demand escalation inbox.
It does not send external messages, monitor patients, continuously watch
the system, or provide an emergency response service.

## Notices

The inbox derives notices from the current implementation register and the
existing verified regression monitoring overview. It highlights overdue
pilot reviews, overdue implementation actions, high/critical incidents
and risks, blocking version changes, unavailable linked evidence, due
regression assessments, missed intervals, incomplete or invalid tests,
long-running occurrences, descriptive deterioration and insufficient
matched coverage.

Operators see notices for records they own; administrators see all notices
including ones whose owner has become inactive. Observers may view notices
but cannot acknowledge them. Reviewers and vendors have no access.
Source descriptions and clinical text are not copied into the inbox.

## Acknowledge and investigate

Acknowledgements are personal, durable and bound to the current notice
identity (including source revision or monitoring report digest). A changed
source creates a new notice identity when its binding changes. An
acknowledged critical notice remains visible while its source is open;
acknowledgement never resolves the source, changes pilot status, hides
notices for other users or authorizes deployment. Use the source register
for investigation, resolution and any administrative pause.

## Integrate with local IT

An approved trusted local administrator can export machine-readable notice
metadata for an existing institution-controlled scheduler or messaging
system:

    python -m caeval.institution_escalation_cli /srv/caeval/workspace \
      --as-admin ADMIN_ACCOUNT --confirm-trusted-local-export

This command requires an active administrator and trusted OS access.
It is not a remote authentication interface. The JSON includes identifiers,
responsible owner, priority, notice type, internal route and the selected
administrator's personal acknowledgement timestamp. It contains no raw
case text, response text or free-form incident notes. The institution must
independently approve the destination, frequency, retention, confidentiality
and delivery policy before routing metadata externally.

The export is a current inventory, not a durable delivery queue. A
nonzero exit indicates the inventory is unavailable and should be
investigated; an empty list is not proof of safety. Existing verification
may be expensive for large workspaces, so measure performance before
deploying recurring polling. No external notification is configured or
started by this feature.

The stopped-service backup procedure preserves the acknowledgement audit
history. Source verification, clinical incident handling, and real-world
institutional acceptance remain necessary.
