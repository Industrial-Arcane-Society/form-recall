# F5.106 T10 mesh-only evidence

Fixed prompt: `同步带轮 T10 节圆直径95.493 齿数30 宽度16 内孔10`

- `legacy_t10_baseline.json`: old `Parser.parse → Document.build()` report and separate failed B-Rep attempt.
- `legacy_document_build_raw.stl`: old raw mesh baseline (1,592 triangles; not a valid-solid volume oracle).
- `legacy_document_build_brep_failed_candidate.brep`: retained failed candidate only; 31-component compound, not used as a B-Rep oracle.
- `rust_quick_mesh_only.stl`: Rust Quick test output captured through `F5_106_RUST_STL`.
- `surface_comparison.json`: raw-vs-Rust sampled surface, topology, bounds, and per-record diagnostics.
- `compare_legacy_raw_to_rust.py`: rerunnable comparison using `tools.compare_mesh_surfaces._compare_pair`.
- `legacy_t10_mesh_matrix.json`: three parameterized old Parser→Document.build raw STL capture cases (includes a nonzero center applied to parsed Feature params).
- `capture_mesh_matrix.py`, `rust_raw_*.stl`, `legacy_raw_*.stl`: parameterized oracle matrix and captured meshes.
- `compare_parameter_matrix.py` / `surface_comparison_matrix.json`: rerunnable per-case surface, topology, bounds, binary face-record, and SHA-256 report.
- `capture_legacy_baseline.py`: rerunnable old Python capture.

Main report: [`docs/06-roadmap/F5.106-quick-timing-pulley-t10-mesh-only.md`](../../docs/06-roadmap/F5.106-quick-timing-pulley-t10-mesh-only.md).

Rust test plus comparator replay from repository root:

```powershell
$env:CARGO_BUILD_JOBS = '1'
$env:F5_106_RUST_STL = (Join-Path (Get-Location) 'artifacts/f5.106-timing-pulley-t10/rust_quick_mesh_only.stl')
cargo test --manifest-path 'src/apps/desktop/src-tauri/Cargo.toml' legacy_t10_quick_raw_mesh_is_mesh_only_and_roundtrips_exports --lib -- --nocapture
cargo test --manifest-path 'src/apps/desktop/src-tauri/Cargo.toml' legacy_t10_aimodel_import_rebuilds_visible_mesh_and_active_record_without_occt --lib -- --nocapture
Remove-Item Env:\F5_106_RUST_STL
python artifacts/f5.106-timing-pulley-t10/compare_legacy_raw_to_rust.py
python artifacts/f5.106-timing-pulley-t10/compare_parameter_matrix.py
```

`stats.volume` is null for all shared Quick mesh-only routes; the signed mesh value is retained separately as `mesh_signed_volume_diagnostic_mm3`. T10 AIMODEL files carry a versioned compatibility marker with kind and CADPlan fingerprint. The Open Project route verifies that marker, the plan gate, and feature rows, then rebuilds the legacy mesh without OCCT and commits the visible active mesh. Unknown or mismatched markers fail closed. CATPart guard tests exercise the shared refusal function used before adapter invocation.
