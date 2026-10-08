# F5.107 — EGT SW50/SW80 legacy Quick mesh migration

## Implemented scope

The Rust parser now recognizes exact catalog-backed `SW50xN` and `SW80xN` product references and emits one parameterized `extrude_holes` CADPlan feature. The plan preserves section envelope, length, extrusion axis/base, all measured through-hole diameters/axes/coordinates, 24-segment rings, and the measured reference frame. Quick routes the exact single-feature plan into the Rust triangle-mesh path; AIMODEL stores the CADPlan plus a fingerprinted mesh-only marker. The active-model import path validates the marker/fingerprint/feature rows and rebuilds through the same mesh-only path. CATPart remains unavailable.

The geometry intentionally reproduces the old Quick behavior: a rectangular envelope plus measured hole positions. The catalog values come from measured EGT data, but this mesh is **not the actual internal extrusion profile**. It is mesh-only and does not provide a B-Rep or CATPart.

## Legacy oracle evidence: EGT001 SW80x088

The original Python path was run directly: `Parser.parse("EGT001 SW80x088")` returned one `extrude_holes` feature with 13 holes; `Document.build()` plus the legacy STL exporter produced the raw-mesh oracle. The measured tunnels contain 15 intersecting axis pairs. The legacy builder does not boolean-union or reject those crossings; Rust preserves that raw surface behavior and reports the geometric self-intersection risk separately.

The Rust mesh was generated through `f5_107_egt_sw_validate`, then compared by the repository BVH-based `tools/compare_mesh_surfaces.py` implementation:

| Measurement | Legacy raw mesh | Rust mesh | Difference |
|---|---:|---:|---:|
| Feature parameters | 1 `extrude_holes`, 13 through holes | Exact parameter match | none |
| Triangles | 1,464 | 1,464 | 0 |
| Bounds (mm) | `[-40,-44,-40] .. [40,44,40]` | same | 0 mm |
| Signed triangle-mesh volume | 426,816.8621298078 mm³ | 426,816.8621298074 mm³ | 4.66e-10 mm³ |
| Boundary edges (weld 1e-8 mm) | 0 | 0 | 0 |
| Non-manifold edges | 0 | 0 | 0 |
| Degenerate triangles | 0 | 0 | 0 |
| Area-weighted sampled surface p95 | — | — | ~1.28e-13 mm |
| Symmetric sampled maximum | — | — | ~3.76e-12 mm |

All 64- and 128-pixel common-frame XY/XZ/YZ projection masks had IoU 1.0 and zero XOR pixels. Surface samples use four deterministic barycentric samples per triangle and exact point-to-triangle distance through the BVH. This is a sampled approximation, **not a continuous Hausdorff guarantee**. The equal edge topology and signed volume also do not negate the 15 geometric tunnel intersections.

## Boundary-edge count discrepancy

The prior scratch note cited 352 boundary edges when counting directly from `Document.build()` polygons. I repeated the same SW80x088 parse and separated the three stages at a shared 1e-8 mm weld tolerance:

- The 1,152 in-memory polygons have 0 boundary and 0 non-manifold polygon-loop edges.
- Legacy `_safe_tris()` converts them to 1,464 triangles with 0 boundary, 0 non-manifold, 0 orientation-conflict, and 0 degenerate edges.
- The exported binary STL, after float32 coordinate storage, has the same topology at both 1e-8 mm and 1e-6 mm tolerances: 0 boundary and 0 non-manifold edges.

Thus this sample does not support float32/STL quantization as the cause of 352. That number is not reproducible by polygon-loop, pre-export triangle, or post-export STL incidence counting; it was a prior counting-method discrepancy. The detailed stage counts are in `comparison.json`.

Evidence files: `comparison.json`, `rust_report.json`, `rust_parameters.json`, `legacy_sw80x088.stl`, `rust_mesh.stl`.

## Validation and limits

- `cargo run --bin f5_107_egt_sw_validate -- EGT001:SW80:88 ...` completed successfully and produced the Rust mesh.
- `compare_sw80x088.py` completed successfully with exact parameters and sampled surface equivalence.
- The AIMODEL reopen, tampered fingerprint/features, markerless refusal, and CATPart guard have targeted Rust regression tests added in `native_model_io.rs`. The earlier broad retry was `cargo test --manifest-path src/apps/desktop/src-tauri/Cargo.toml native_quick_egt_sw --lib`; Cargo compiled the test binary, then reported `process didn't exit successfully ... (exit code: 0xc0000139, STATUS_ENTRYPOINT_NOT_FOUND)`.
- The requested one-time targeted retry was `cargo test --manifest-path src/apps/desktop/src-tauri/Cargo.toml --lib native_model_io::tests::f5_107_egt_sw_aimodel_marker_reopen_and_catpart_guard_fail_closed -- --exact --nocapture`. Cargo compiled successfully (38.26 s), but Windows could not launch `ai_modeler_ui_lib-6dae68ff4a58695d.exe`; stderr was `error: test failed, to rerun pass --lib` / `process didn't exit successfully ... (exit code: 0xc0000139, STATUS_ENTRYPOINT_NOT_FOUND)`. No underlying missing DLL/entrypoint name was reported, so the assertions remain unexecuted and the exact OS loader dependency is unknown.
- Full parse-only verification of all 1,068 SW50/SW80 text variants is not run in this turn.
- Full geometry generation and old-mesh comparison for the remaining 266 profiles is **not run**. No 267-profile geometry coverage claim is made.
- No OCCT/CATIA operation or CATPart export was invoked.
