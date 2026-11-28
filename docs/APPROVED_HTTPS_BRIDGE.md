# Approved external HTTPS candidate gateway

The institutional evaluation engine still makes candidate calls only to
literal loopback HTTP. For an institution that has explicitly approved an
external product, a separate trusted gateway can translate the existing
single-turn or conversational connector to one fixed HTTPS endpoint.

This does not approve transfer of clinical records. Only institution-approved
synthetic or non-identifiable evaluation content may be used. A configuration
label cannot establish de-identification, consent, a legal basis, vendor
retention terms, or clinical data residency. IT and the clinical owner must
approve the exact endpoint, product/version, data, jurisdiction and routing.
The remote vendor may retain inputs; review its terms independently.

## Stage configuration

Keep a trusted regular JSON configuration outside the evaluation workspace.
Do not make it group/world writable. The following is illustrative; replace
the placeholder IP with the independently verified public IP of the approved
hostname. The example hostname is not an actual configured provider.

    {
      "vendor_id": "candidate",
      "target_name": "Approved product",
      "target_version": "1.0",
      "modality": "conversation",
      "listen_port": 18867,
      "upstream_url": "https://api.example.com/v1/chat/completions",
      "upstream_ip": "REPLACE_WITH_APPROVED_PUBLIC_IP",
      "upstream_input_field": "messages",
      "upstream_answer_path": "choices.0.message.content",
      "fixed_fields": {"model": "approved-model", "stream": false},
      "local_token_env": "CA_BRIDGE_LOCAL_TOKEN",
      "upstream_token_env": "CA_BRIDGE_UPSTREAM_TOKEN",
      "upstream_auth": "bearer",
      "timeout_seconds": 30,
      "max_calls": 1500,
      "max_seconds": 1800,
      "data_scope": "approved_synthetic_or_nonidentifiable"
    }

Single-turn mode accepts one prompt string and maps it to the declared
upstream field. Conversation mode translates the existing patient/system
transcript roles to user/assistant, sends complete visible history, and
uses no upstream session identifier. Fixed scalar fields can select a model
or fixed sampling configuration. No streaming, arbitrary URL, tools,
additional per-call fields, redirects or retries are supported.

Describe the binding without making network calls:

    python -m caeval.approved_https_bridge describe /etc/caeval/bridge.json

Review the binding hash and the generated existing connector JSON. Stage
that connector in approved-connectors or approved-conversations as
appropriate. Its loopback URL contains the exact bridge configuration digest.
A changed configuration creates a different URL and requires a new frozen
job or collection approval. Never edit a bound job to redirect it.

Generate a separate random 32-byte local bearer token and provide its
64-character lowercase hex representation via the approved environment
variable to the bridge and candidate worker. The upstream credential
belongs only in the bridge process environment. Do not put actual
credentials in configuration, workspace, command arguments or service logs.

Run as a separate trusted account with explicit approval:

    python -m caeval.approved_https_bridge serve /etc/caeval/bridge.json \
      --approve-binding EXACT_HASH --confirm-external-transmission

The service binds only 127.0.0.1, verifies the local bearer and exact path,
and sends a bounded JSON request to the fixed numeric public address over
TLS, authenticating the approved hostname using the system trust store.
No DNS resolution or environment proxy is used for the upstream request.
Only successful JSON responses with the selected text field are returned.
Failures consume the call budget. Errors do not include response bodies.
A vendor can include arbitrary text in a successful answer; do not assume
the answer is free of sensitive content.

The gateway enforces the configured per-process call budget and stops
accepting new work after its deadline. Socket timeouts are not a strict
wall-clock deadline for a slow peer; supervise the service and enforce an
OS-level runtime limit. Do not automatically restart it, since restart
would create a new budget. Pin the approved outbound address and port in
the institutional firewall; firewall policy is independent of the application.
Reapprove changes in endpoint, public address, model, credentials or data
scope. An IP pin can break when a vendor rotates infrastructure; fail closed
rather than fall back to DNS or a new destination.

Use the existing trusted single-turn collection or patient conversation
worker, with all its original approval and scientific verification gates.
A valid bridge response does not establish the actual deployed model
identity or version. Live provider interoperability, certificate policy,
network isolation and institutional security acceptance remain necessary.

## Native contracts

For an upstream implementing a native Clinical AI Eval contract, replace the
three legacy mapping fields (upstream_input_field, upstream_answer_path,
fixed_fields) with protocol. Use the matching modality: single_turn with
clinical-ai-eval-candidate/1, conversation with clinical-ai-eval-conversation/1,
citation with clinical-ai-eval-citation-trace/1, or interoperability with
clinical-ai-eval-interoperability/1. The bridge validates the exact approved
product/version, preserves correlation and input, and requires a valid native
upstream response. It never synthesizes the vendor's identity echo. Legacy
mapped adapters remain supported. Interoperability is fixed synthetic data only.
Separate operator approval, pinned HTTPS, and call budgets still apply.
