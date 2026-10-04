# F7 visual reference binding

## Scope

This change makes visual review association fail closed when required source identity is missing. A review can contribute to current reference coverage only when its stable reference ID resolves to the active manifest, its `source_sha256` matches that manifest, and its `observation_id` and source hash match the current full-image inventory.

## Acceptance

- A fully bound review remains comparable and can cover required evidence.
- Missing review `source_sha256` makes the review non-comparable.
- Missing review `observation_id` makes the review non-comparable.
- Missing current inventory makes the review non-comparable.
- Missing current inventory `observation_id` makes the review non-comparable even if the remaining inventory fields would otherwise validate.
- In each missing-binding case, the visual summary cannot report all original references covered.

## Evidence and boundary

The regression test `visual_coverage_requires_complete_reference_observation_binding` uses synthetic manifest, inventory, and review records. It covers one valid association and each missing-field case without invoking ACP, DSH, a provider, or GUI. This change closes a local evidence-association gap; it does not establish real-model visual acceptance or claim that any particular provider run failed for this reason.

## Verification

- `./scripts/test-lib.ps1 -Filter native_state_summary::tests -Offline` — 6/6 passed, including the new binding matrix.
- `rustfmt --check src/apps/desktop/src-tauri/src/native_state_summary.rs` — passed.
- `git diff --check -- src/apps/desktop/src-tauri/src/native_state_summary.rs docs/06-roadmap/66-f7-visual-reference-binding.md` — passed.
- Repository-wide `cargo fmt --all --check` reports formatting diffs in unrelated existing worktree files; this change's Rust file passes its targeted rustfmt check. Those files were not modified for this task.

No ACP/DSH provider, GUI, or real model visual acceptance was run.
