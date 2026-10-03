# F7 finish evidence binding — implementation and verification

## Finding

Before this change, the host considered a stored finish current when only the CAD revision and geometry fingerprint matched. Visual inventory/review, reference-manifest metadata, unresolved findings, construction milestones, and external facts can change without changing either value. `current_model_summary` also recomputed the visual summary while continuing to expose the old accepted finish. That allowed a same-revision, same-geometry model to appear accepted against stale acceptance evidence.

## Implementation

- `native_finish_evidence` creates a compact canonical JSON snapshot. It recursively sorts object keys, preserves array order, serializes compact UTF-8 with `serde_json`, and stores only the SHA-256 digest in `finish_summary.evidence_fingerprint`.
- The digest binds the full CADPlan and source provenance, the freshly recomputed native host aggregate, normalized original reference images and authoritative user turns, visual summary and inventory/review histories, the finding ledger, construction milestone, required external facts, and current complete/partial preview geometry checks.
- `cad_finish_model` records the digest only after the finish assessment and export complete. `current_model_summary` recomputes it on every read; missing or changed evidence hides the finish report and returns `candidate` / `not_finished`.
- `model.changed` now carries the host-derived `acceptance_confirmed` value and current host visual/geometric status. Vue accepts a model only when that explicit host confirmation is true.

## Regression coverage

At unchanged CAD revision and geometry fingerprint, Rust regressions verify that changed visual inventory, visual review, original reference manifest, finding ledger, and external facts invalidate an earlier accepted finish. Restoring the original evidence restores the same digest and accepted host status. The snapshot unit tests also cover construction milestone, local geometry checks, and CADPlan source provenance changes, and verify canonical object-key ordering.

Targeted local checks:

```powershell
Set-Location src/apps/desktop/src-tauri
pwsh -NoProfile -File scripts/test-lib.ps1 -Offline -Filter native_finish_evidence::tests
pwsh -NoProfile -File scripts/test-lib.ps1 -Offline -Filter same_revision_visual_reference_findings_and_facts_changes_expire_finish_binding
pwsh -NoProfile -File scripts/test-lib.ps1 -Offline -Filter current_model

Set-Location ../
node --test src/agentStatus.test.mjs
node --test src/App.agentEvents.integration.test.mjs
```

The checks above exercise Rust unit logic and the headless Vue event path only. No GUI, DSH, model provider, or CATIA session was started; production provider behavior is therefore unverified here.

## Todo update suggestion

- **P0 done:** bind finish acceptance to a canonical host evidence snapshot, invalidate same-revision stale finish evidence, and require host terminal confirmation in Vue.
- Keep the real installed-app DSH multimodal acceptance run as a separate verification item; the unit tests do not establish runtime provider/image behavior.
