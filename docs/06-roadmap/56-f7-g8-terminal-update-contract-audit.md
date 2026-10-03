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

The redacted Run13 gate reason establishes only that the current evaluator observed more than one terminal-status event in at least one aggregated CAD call row. It does not establish the number of affected calls, whether IDs were present, which terminal statuses occurred, their order, whether sanitized update fields agreed, or whether the collector observed one incoming notification more than once. Static code and the gate reason are insufficient to choose a correctness-preserving coalescing rule.

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

## Verification performed

`node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs` passed 51/51 existing tests. These tests include missing-ID non-coalescing and rejection of two terminal updates with one valid ID. They validate the current strict behavior, not the classification of the Run13 event or a replay-safe repair. No runtime or external system was started.
