# F5.107 EGT SW Quick mesh parity probe

The Rust validator defaults to one low-cost representative profile, `EGT001:SW80:88`. It emits a Rust binary STL and parameter/geometry diagnostics. It does not invoke OCCT and does not build CATPart.

From repository root:

```powershell
cargo run --manifest-path src/apps/desktop/src-tauri/Cargo.toml --bin f5_107_egt_sw_validate -- EGT001:SW80:88 artifacts/f5.107-egt-sw
$env:PYTHONPATH = 'src/legacy'
python artifacts/f5.107-egt-sw/compare_sw80x088.py
```

The Python comparison runs the original `Parser.parse("EGT001 SW80x088") -> Document.build() -> exporters.export_stl` path. It uses `tools/compare_mesh_surfaces.py` for triangle counts, bounds, signed mesh volume, welded topology, common-frame projections, and symmetric area-weighted barycentric samples with exact point-to-triangle distances through a triangle BVH. The sampled distance is not continuous Hausdorff distance. Intersecting measured tunnels can make this legacy mesh geometrically self-intersecting even when its edge incidence is manifold; those are reported separately.

The CLI accepts other exact catalog IDs for targeted runs. It does not run the full 267-profile geometry batch. Parser corpus validation is a separate parse-only task and must not be interpreted as mesh parity.
