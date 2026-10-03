# F7 G8 ACP terminal update contract audit

## Scope and evidence boundary

This is a static audit of the ACP result gate, the Node event collector, and the repository's ACP process bridge. The Run13 review was limited to the reported gate name `acp_cad_tool_result_terminal_update_duplicated`; no raw trace, event IDs, tool arguments, error text, thought/provider response, image, GUI, DSH, CATIA, or provider was inspected or started. The checkout used for this audit was based on `1882053aef2b8eca3e00bedd6ff12b4f086b794c` in an isolated worktree.

## Source contract findings

- `native_agent_coldstart_acceptance.mjs` records each ACP `tool_call` and `tool_call_update` with event kind, optional title/status, call ID, timestamps and run/session/turn scope. It fingerprints `rawInput` only on the initial `tool_call`. It does not record a notification sequence ID or a safe digest of terminal update fields.
- `native_agent_coldstart_gates.mjs` aggregates events by `tool_call_id` and increments `terminal_updates` for every `tool_call_update` with status `completed` or `failed`. Any CAD row with more than one such event fails `acp_cad_tool_result_terminal_update_duplicated`. This detects multiple recorded terminal-status notifications, not whether they are retransmissions, equivalent state upserts, or conflicting state changes.
- `src/acp.rs` dispatches incoming ACP JSON-RPC notifications to subscribers without deduplication. JSON-RPC notifications have no response ID; the repository does not add a transport/event identity at this layer.
- The ACP v1 tool-call contract describes tool calls as updates keyed by a session-unique `toolCallId`; status describes the current execution status (`pending`, `in_progress`, `completed`, `failed`). The protocol does not define a notification sequence number or an at-most-once constraint for terminal-status notifications. Therefore the protocol alone cannot establish whether two terminal notifications are a legitimate repeated upsert, duplicate observation, or a conflicting lifecycle report.

Protocol reference: [ACP v1 Tool Calls](https://agentclientprotocol.com/protocol/v1/tool-calls). Production runtime metadata pins ACP SDK v1.4.0 in `dsh_runtime.rs`.

## What can and cannot be concluded

The first redacted Run13 gate reason established only that the current evaluator observed more than one terminal-status event in at least one aggregated CAD call row. A later whitelist-only summary contained 38 diagnostics; the duplicate-terminal gate failed, `acp_cad_tool_call_id_missing` was absent, `isError=true` count was zero, and all four error/error-info/name/code presence flags were false. This narrows the duplicate observation to a nonempty-ID aggregation group and provides no Cordis tool-error presence signal. It still does not identify the group, terminal statuses/order, payload equality, or whether the collector observed one incoming notification more than once. No IDs, ordering, payloads, or raw events were read. Static code and the permitted summary remain insufficient to choose a correctness-preserving coalescing rule.

Do not resolve this by lowering the threshold, ignoring one status, choosing the last status, or deduplicating solely by `(tool_call_id, status)`: each could conceal a real `completed`/`failed` conflict or erase a later meaningful state change. No production gate change is made in this audit.

## Regression matrix required before a repair

Keep the existing cases that reject two terminal-status updates under a call ID and that distinguish missing call IDs. Extend fixtures only after the production contract for terminal updates and collection is settled, covering at least:

1. One invocation with one successful terminal update passes the result gate.
2. One invocation with one failed terminal update fails with the failed-result reason.
3. Repeated equivalent terminal upserts, if the pinned ACP/Cordis production contract permits them, are handled idempotently only when equivalence can be proven from a safe, complete event identity or canonical payload digest.
4. Different terminal states for one invocation remain a hard conflict, independent of ordering.
5. A missing ID, missing terminal event, out-of-scope event, or malformed identity never coalesces with another call.
6. Repeated identical CAD invocations remain individually paired to bridge calls; terminal-update deduplication must not weaken the existing interval/argument/name/session matching gates.
7. Collector/adapter replay tests distinguish one upstream notification delivered twice by the adapter from two separately emitted notifications where the source contract exposes sufficient identity. If the protocol supplies no such identity, preserve ambiguity as a hard failure.

The fixture must model only fields exposed by the real adapter. Any new safe telemetry should be limited to counts, ordering/status classes, and a keyed or cryptographic digest over explicitly defined normalized fields; never persist raw tool content, arguments, error bodies, thoughts, provider responses, or images for this gate.

## Safe diagnostic projection added after Run13

The runner can now append an optional `acp_terminal_update_projection_v1` record to the existing tool-result diagnostics JSONL. It contains counts by broad tool category (`cad`, `non_cad`, `unknown`), event kind, and ACP status enum; a distribution of call-group status-sequence categories; terminal-count histogram; counts of equal/different pairs under the deliberately limited sanitized `(tool category, terminal status)` summary; and missing-ID event count. ACP IDs are used only transiently to aggregate groups and never emitted. Tool titles are reduced to the broad category; arbitrary title strings, payload fields, arguments, error data, and images are never included. Updates without an ID and without a title are classified as `unknown`. The existing strict gate is unchanged.

The equal-summary-pair count means equality only under that safe projection. It is not evidence that full update payloads matched or that a replay occurred. Mixed status summaries show disagreement in the projected terminal status class, not the cause or source of that disagreement. The new fixture verifies aggregation, mixed/same terminal classifications, missing-ID counting, and absence of sentinel IDs/titles/payload fields from serialized output. This improves the next authorized diagnosis without classifying the historical Run13 event.

## Verification performed

`node --check` passed for the runner and gate module. `node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs` passed 52/52. Existing cases still include missing-ID non-coalescing and rejection of two terminal updates with one valid ID. These checks validate the current strict behavior and the privacy of the projection, not the classification of the historical Run13 event or a replay-safe repair. No provider or new cold-start was run for this change.

## Follow-up: prospective run-local projection v2

The runner now emits `acp_terminal_update_projection_v2` and `cordis_binding_projection_v1` when `--tool-result-diagnostics` is enabled. This code change does not reinterpret Run13/Run14 and does not alter any strict gate.

- Terminal update comparison consumes the full ACP `session/update.params.update` object at the event listener. A fresh random HMAC key exists only in the current process; HMACs live in a `WeakMap` attached to normalized event objects and are not serialized. The output contains pair counts for equal/different terminal update HMACs within one call ID, never the HMAC, key, ID, or update body. This establishes equality of the complete parsed ACP update object under the canonical JSON encoding, not delivery replay or causality.
- Tool/status counts and call-group terminal distributions use an explicit allowlist mirrored from the 25 current native Cordis tool schemas. Unknown titles collapse to `unknown`; output statuses and terminal categories are enums.
- Binding counts use the runner's actual ACP event rows, authenticated loopback bridge records, schema/registration names and Rust native audit rows. They report two joins (`acp_to_bridge`, `bridge_to_audit`) by allowlisted tool name and fixed reason codes such as `acp_call_missing`, `identity_pairing_ambiguous`, `bridge_execution_rejected`, `native_audit_missing` and `native_audit_rejected`. IDs, fingerprints, timestamps and free-form error fields are only compared in memory and never emitted. There is no error-body parsing or provider-error classification.
- The gate implementation remains unchanged: repeated terminal updates and failed updates continue to fail. The new projection adds diagnosis only; its reason counts must not be treated as permission to coalesce events or accept a run.

The strict runner was not started while implementing this follow-up. The synthetic runner test suite verifies HMAC equality/difference behavior, allowlist collapse, missing/rejected binding cases and absence of sentinel IDs/hashes/payloads/error bodies from serialized projections.
