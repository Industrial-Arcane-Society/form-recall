# Loop Review Test Status

Date: 2026-10-01

## First MCP workflow regressions

tests/test_dsh_review_workflow.py — **8 passed**.

The first run exposed a finish-recording argument collision (_record(state, ..., state=...)); changed the diagnostic field to finish_state and reran. The passing rerun is preserved in tests-dsh-first-batch-rerun.txt; the original failed trace is preserved in tests-dsh-first-batch.txt.

Covered boundaries:

- geometry edits and adding holes are not gated by a missing visual review;
- finish saves a candidate when reference evidence is missing;
- a low advisory score cannot block complete itemized matching evidence;
- a newer partial result for the same reference/view overrides an older match;
- a non-comparable view pair is diagnostic when another comparable pair supports the same fact;
- flattened BREP hole instances and verified_blind status are checked independently of feature-row count.

## Existing incremental runtime suite

Command: python -m pytest tests/test_agent_runtime_incremental.py -q

Result: **54 passed, 5 failed, 2 subtests passed**. Full output: tests-dsh-first-batch.txt.

| Test | Expected | Actual |
|---|---|---|
| test_coincident_cut_face_is_reported_not_measured | feature_effect.warning contains 完全重合 | feature_effect.warning is None |
| test_each_feature_reports_what_it_did_to_the_material | feature_effect.volume_delta_mm3 == 0.0 | value is None |
| test_full_depth_recess_cut_is_attributed_to_cut_feature | geometry check state == failed | state == needs_review |
| test_hole_opening_clipped_by_the_part_edge_is_caught | geometry check state == failed | state == needs_review |
| test_through_hole_is_checked_against_mesh | through_hole_mesh_checks[0].axis_surface_crossings == 0 | field is absent (KeyError) |

The first two failures are legacy incremental-runtime material-effect return fields. The last three reflect expectations from the mesh-era geometry checker versus the new BREP result vocabulary/instance report. These are cross-owner integration items and have been sent with the exact expected/actual values to the input and geometry owners. No expectations were changed by loop_review.

## Static checks

- python -m py_compile ai_modeler/agent/dsh_mcp_server.py ai_modeler/agent/plan_state.py ai_modeler/agent/llm.py — passed at the first implementation checkpoint.
- git diff --check — no whitespace errors; Git printed line-ending notices for files edited by concurrent owners.

No real DSH UI acceptance has been run at this checkpoint.

## 2026-10-01 MCP/web follow-up

- `python -m unittest tests.test_online_search tests.test_dsh_review_workflow tests.test_agent_runtime_incremental tests.test_cadplan_sdk -v`: **106 passed** in 29.646s. Full log: `artifacts/agent-quality-rebuild/loop-review/full-regressions-source-web-partial-20261001.txt`. Includes mocked online parser/fallback/error checks; does not establish live internet success. New MCP cases cover visible-but-unaccepted partial preview, independent source-only inventory metadata, superseded/active hole status counting, and provenance preservation/correction.
- `cad_inspect_references` now sends one original image and a compact source-only request with `task_kind=vision_review`, `review_complexity=source_inventory`, `max_tokens=1800`, no CADPlan/history; records source SHA, ROI, request profile, usage and an explicit `model_observation_unverified` status. Inspection results remain revisable and do not independently satisfy acceptance.
- Failed geometry preview consumes SDK partial fields and returns its partial image with current revision/generation and applied/failed/related IDs. It stores `candidate_partial_not_accepted`, requested full plan and un-applied IDs separately, never promotes it to `last_preview`/best candidate, allows only diagnostic visual comparison without creating closure evidence, and can export a validated prefix plus traceable full requested plan/report.
- After two same failed previews, edits targeting failed/related feature IDs require an associated ROI/source recheck or a partial visual diagnostic. Text-only tasks do not enter that image-dependent guard.
- Hole summary now recognizes active `verified_counterbore` and `verified_blind_segment`, counts active vs superseded history, and does not require each superseded record to be measured again. Supersession does not count as applied coverage for unrelated missing features.
- `cad_update_feature` preserves sources for unchanged parameters. A changed value whose `user` source is not backed by a matching trusted turn ID and quote is rejected; pure provenance-only correction requires explicit `provenance_corrections` plus the same trusted evidence, with before/after source recorded in action history.
- Online detailed search keeps `online.search()` list-compatible while recording Bing/DDG fallback, cache engine provenance, no-results vs parser/network/timeout/HTTP/mixed failure, source URLs, and opened-page text hashes. The real CLI web request is still pending; earlier CLI evidence only proves the tool was called and returned no parsed rows.
- No full live source-inspection or web-open result is claimed yet. Full cold Agent visual rebuild remains separate and unverified; keep the original failed cold evidence intact.

## Live CLI follow-up: source inventory and web result quality

- Independent image CLI `aq-source-inventory-20261001` completed exit 0 as `conversation_completed`, did not create geometry, and called `cad_inspect_references` once. Actual direct provider wire profile/image hash/usage and the full raw parsed inventory are archived at `artifacts/agent-quality-rebuild/loop-review/cli-source-inventory-20261001/turns/e1ab86d936dd4f08964b78083a437f4f/source-inventory-evidence/`. The request used low effort, `max_tokens=1800`, no thinking budget, one 72,309-byte image with matching source/transport SHA. Provider reported 608 input, 1,759 output, 912 reasoning, 2,367 total tokens in 46.4 seconds. The transport telemetry did not capture `finish_reason`; the MCP JSON parser completed and persisted the structured inventory, but response-limit termination cannot be ruled out. This run used the previous inventory prompt and omitted relative opening sizes and wall-plane relations. A generic schema prompt enhancement is now in code, not yet live-verified.
- Separate real CLI `aq-web-open-20261001` completed exit 0 and produced a locally geometry-accepted 80×50×8 box. It called the real Bing search route three times. Although HTTP/search parsing reported `ok`, results were irrelevant: GitHub ChatGPT topics, Zhihu and Microsoft Translator for `AA6061-T6 density`; the chosen Bing redirect opened only 65 characters (`SHA-256 35f1aee2ad38d19510c5a0d1969f71110ad609073870bd6dfdfd3ac9a3d4f285`). No usable density evidence was read. This is a failed web fact lookup despite the accepted geometry state; do not cite it as web success. Raw session state/output is at `artifacts/agent-quality-rebuild/loop-review/cli-web-open-20261001/turns/d7eee525babf481d9a0c7d8007f915fa/`.
- The old live web session loaded the pre-filter online module. Bing redirect decoding and query relevance filtering are now implemented in the checkout but still require unit and live verification after the current cold CLI acceptance terminates. External facts explicitly required by a user must remain unresolved when searches are irrelevant or page bodies contain no usable evidence; the CLI run exposes that acceptance/reporting gap.
