# F7/G8 safe native audit error-class publication

## Gap

`summarizeCordisBindingDiagnostics` already classifies a matched native audit rejection as one of the fixed classes `argument_validation`, `state_conflict`, `execution`, or `unknown`. The final `safeBindingProjection` omitted that field, so `tool-result-diagnostics.jsonl` retained the rejection outcome but discarded its safe classification.

## Change

The publication boundary now preserves `error_class` only for `stage=bridge_to_audit` and `outcome=native_audit_rejected`. It accepts only the four fixed classes above; a missing or invalid value becomes `unknown`. Other outcomes never carry `error_class`. The published projection continues to omit call IDs, ACP IDs, argument fingerprints and values, timestamps, and free-form error details.

## Verification

- `node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs`: 132 passed, 0 failed.
- The new synthetic integration regression calls the production Cordis summarizer and `writeSafeAcceptanceArtifacts`, then reads the emitted `tool-result-diagnostics.jsonl`. It verifies a successful audit has no error class; rejected audits preserve each allowed class; an invalid class is published as `unknown`; and sentinel IDs, fingerprint, argument value, error text, and invalid class text are absent.
- `git diff --check`: passed.
- No DSH/provider request or real cold-start was run; this is a local safe-publication contract test, not evidence of a production rejection being observed.

## ACP-to-audit cross-count decision

No additional ACP-terminal-to-native-audit cross-count was added. The current diagnostic already reports ACP terminal outcomes separately and correlates ACP, bridge, and audit records transiently before emitting aggregate rows. Exposing a joined terminal-status/error-class matrix could be done without IDs if it used only the validated same-run identity join, but it adds new projection semantics for missing, ambiguous, repeated, and contradictory terminal updates. The specific gap here is loss of an already-computed allowlisted audit class, and the existing bridge-to-audit aggregate fixes it without expanding the schema. Revisit the cross-count only if a concrete diagnostic question needs that relationship.
