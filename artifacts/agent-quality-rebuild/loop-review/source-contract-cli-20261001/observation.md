# Source contract CLI observation

## Run identity

- Invocation: `python -m ai_modeler agent --offline --session-id geometry-local-material-source-contract-20261001 --output-dir artifacts/agent-quality-rebuild/loop-review/source-contract-cli-20261001/output --text-file artifacts/agent-quality-rebuild/loop-review/source-contract-cli-20261001/prompt.txt`
- User turn ID: `389d8bc080dc446198ddb44e3e57be17`; one text-only turn, no reference images.
- DSH session: `geometry-local-material-source-contract-20261001-dsh-76d15faafaab`.
- Outcome: CLI exit `0`, `accepted_and_verified`, finish state `accepted`.
- Elapsed: `226875 ms` (3m 46.9s).
- Tool actions: `12`; one `cad_create_model`, three successful `cad_add_feature` actions, no `cad_remove_feature`, no model replacement. One `cad_add_feature` was rejected because the agent initially marked computed radius 5 as user-sourced; the error requested `cad_get_state.authoritative_user_turns`. The agent then correctly recorded radius 5 as derived from user diameter 10. No delete/recreate loop followed.

## User-source audit

The following directly represented scalar values were stored as `type=user` and have a current-turn ID plus literal source quote in the MCP action audit:

| CADPlan parameter | Value | User parameter mapping | Quote | Turn ID |
|---|---:|---|---|---|
| `base-plate.length` | 100 mm | length | `底板长100` | `389d8bc080dc446198ddb44e3e57be17` |
| `base-plate.width` | 60 mm | width | `宽60` | `389d8bc080dc446198ddb44e3e57be17` |
| `base-plate.height` | 20 mm | thickness | `厚20` | `389d8bc080dc446198ddb44e3e57be17` |
| `blind-hole-cut.height` | 6 mm | blind depth | `从顶面向下深6` | `389d8bc080dc446198ddb44e3e57be17` |
| `revolve-boss.angle` | 360° | rotation angle | `绕Z轴360度旋转` | `389d8bc080dc446198ddb44e3e57be17` |
| `revolve-boss.center` | `[-25, 0, 0]` | center | `中心(-25,0)` | `389d8bc080dc446198ddb44e3e57be17` |

The numeric audit also confirms that the direct board thickness was semantically mapped to the stored box height and blind depth to the stored cylinder height. The evidence text is a literal substring of the authoritative turn. `turn_id` is transient by the current CADPlan schema and is stripped from plan sources after validation; the action audit retains it.

These values are represented by genuine geometry conversions, so their stored CADPlan source is `derived`:

- Through-hole diameter 10 mm becomes cylinder radius 5 mm; evidence states diameter-to-radius conversion.
- Blind-hole diameter 8 mm becomes cylinder radius 4 mm; evidence states diameter-to-radius conversion.
- The revolve profile `[[0,20],[6,20],[6,28],[0,28]]` is derived from outer diameter 12 mm, height 8 mm, and the top-face z position.
- Cutter centers and extents are derived coordinates from the user XY locations, top plane, thickness, and depth.

Schema boundary: the current CADPlan feature stores `radius` and an aggregate `profile`, not separate `diameter`, `outer_diameter`, or boss-height scalar fields. The action audit therefore has no separate `type=user + turn_id` row for the input diameters or the two dimensions encoded by the revolve profile. Their derived source evidence describes the conversion and numeric inputs. If acceptance requires an independent machine-readable user-turn audit for every pre-conversion input dimension, the current parameter-source schema cannot express that; it needs a dedicated dimension-claim audit alongside the CADPlan sources. No directly stored scalar value was relabeled as derived to evade its user source.

## Geometry evidence

- BREP: `passed`, valid `True`, solid count `1`, connected components `1`.
- Measured bounding box: `[100.0, 60.0, 28.0]` mm; volume `119032.389 mm³`.
- Through hole: measured diameter 10 mm, `verified_open`, through intent true.
- Blind hole: measured diameter 8 mm, measured depth 6 mm, `verified_blind`.
- Hole measurement is complete: `True`; CATIA was not invoked. There were no reference images, so this is local geometry/source evidence rather than visual reference acceptance.

## Provider and runtime evidence

- Actual provider observations: `13` attempts, `13` model calls (source `dsh_llm_stream_events`), wire observed `True` at `node_global_fetch`.
- Model: `moonshotai/Kimi-K2.7-Code`; all `13` calls used reasoning effort `low`.
- Provider-reported usage: `179013` input, `6530` output, `4503` reasoning tokens; usage source `provider_response_body`.
- Visual review pairs: 0; no images were supplied.

## Verification

- `python -m unittest discover -s tests -p test_dsh_review_workflow.py`: 39 tests passed.
- MCP stdio smoke: initialize, tools/list, and tools/call(cad_get_state) passed; authoritative_user_turns was present.
- Non-comparable review mock: server discarded one model-supplied local match, cleared score/coverage/hole count, returned `not_comparable`, and counted zero independent valid pairs.
- `git diff --check`: exit 0. Git printed CRLF/LF working-copy warnings for the already-dirty tree.

## Evidence files and hashes

- Prompt: `769857cf5578f014b6452fad4580766822c181d6ecddfd3a5962a3cf685cf141`
- CLI log: `5dec6142457cfe1a3c49da971f8cc3c9aa0156b4e6db384b2428b1e77d5b1ff6`
- Agent observability JSONL: `c4c80d0b7925f0ef67085939d58bb963c34dc88a487b7c0488152243b2bc4e72` (`224` events).
- Result JSON: `ff1b9af66cac63f5556fe3654a6f137661d7df1389c391662289a28d7f196e30`
- Geometry report: `adcdb50040ae662c221910638cee711acca890ffb9c8c2633a6ac6115bbb2989`
- MCP run-state audit copy: `942bd9a211b7ba1e58ae43186d2cf60bf25907c1228932c27a06e991f17cbfc1`

## Final code hashes

- `ai_modeler/agent/dsh_mcp_server.py`: `d4071b0af89dd988ee6c705fade5b0ee2b11894d1d43eb9c80f07d79dfe01124`
- `prompts/dsh_agent/system.md`: `e591fd2155773004649ce18e8cf07135974d49e193ab1606b9ebd78063e9b86d`
- `tests/test_dsh_review_workflow.py`: `5850cd584307bf8a76050e4a0a0bccc0ab6170b87608658ed52128b64985bf04`
