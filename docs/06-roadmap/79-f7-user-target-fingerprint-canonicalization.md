# F7: unify review and summary target fingerprints

Date: 2026-10-04

## Change

The native review producer and state-summary consumer now use one canonical
`user_target_fingerprint` function. It hashes only model-target turns, represented
as `turn_id`, the original user prompt, and normalized/deduplicated reference IDs.
Host bookkeeping such as `session_id`, `at_epoch_ms`, `created_at`, and
`intent_source` is excluded. Intent and the existing model-target classification
rules still decide which turns participate in the hash.

This keeps stale-review protection bound to changes in the target text or its
reference set while preventing host metadata from making a valid review invisible
to the state summary. No visual-coverage, mismatch, finding, geometry, or finish
acceptance gate was weakened.

## Regression coverage

- A unit test checks that raw host-shaped and normalized turns have the same
  fingerprint; metadata-only changes keep it stable; prompt or reference changes
  produce a different fingerprint.
- The native executor integration test writes a host-shaped user-turn record,
  runs reference inspection and a real mock-provider review through the native
  tool executor, then reads `cad_get_state`. It requires one current comparable
  review pair and covered evidence. Changing only timestamp/classifier metadata
  preserves that review. Changing the original user target text makes the old
  review stale and returns the required evidence to missing/uncovered.

## Compatibility and verification boundary

Review records created before this canonicalization may carry the previous
whole-turn-manifest hash. They remain in history but will not be treated as current
evidence; the image must be reviewed again. This preserves fail-closed stale
evidence handling.

The tests exercise Rust-native review/state-summary integration with a local mock
vision provider. They do not establish visual correctness for a real provider,
CATIA compatibility, or the result of a new cold-start acceptance run.

## Verification performed

- The feature worktree compiled the targeted integration test with
  `cargo test --manifest-path src/apps/desktop/src-tauri/Cargo.toml
  native_executor_inspect_current_review_and_get_state_preserve_current_bindings
  --no-run` and `TAURI_CONFIG={"bundle":{"resources":[]}}`.
- The feature worktree's direct test executable exited before the Rust harness
  with Windows loader error `0xC0000139` (`STATUS_ENTRYPOINT_NOT_FOUND`). After
  cherry-picking both commits into the integration worktree, the repository's
  supported offline wrapper `src/apps/desktop/src-tauri/scripts/test-lib.ps1
  -Offline` passed **346/346 Rust tests**, including the canonicalizer and native
  executor regressions.
- `git diff --check` passed before commit. A whole-file `rustfmt --check` on the
  three touched Rust files reports formatting changes beginning in untouched
  baseline code; applying it would create unrelated churn, so no broad reformat
  was applied.

The passing native tests verify the producer/consumer binding behavior. A new
real DSH cold-start run is still required to establish whether this repairs the
Run23 visual-review gate failure.
