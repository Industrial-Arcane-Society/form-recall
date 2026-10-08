# F5.78 Legacy raw STL vs Rust headless Quick

Captured from existing TEMP files on 2026-10-08. No cargo/rustc, rebuild, or regeneration was run. Windows paths below use forward slashes to remain readable in Markdown. Exact inputs are copied from each run's active-model source_prompt.

## Evidence policy

Each per-case comparison.json contains only the named legacy_raw_vs_rust comparison plus source/input provenance. Repeated placeholder BREP comparisons (legacy_brep_oracle_vs_rust and legacy_raw_vs_legacy_brep_oracle) are excluded and not evidence. The source report top-level passed is excluded and not accepted. The retained comparison's surface_equivalence_passed is solely the raw-vs-Rust surface comparator result.

All coordinates use shared world coordinates in millimetres without normalization/alignment. Reported mesh tolerance is 0.08 mm.

## Cases

### case-1: matrix-two-solids

- Exact input prompt: 圆柱直径20高30，再做一个长方体长100宽60高20
- Source report: C:/Users/lycohana/AppData/Local/Temp/f5-mesh-f578-case-1-20261008.json
- Source report SHA-256: e37509c71cb448eaad35b92d83e4ac858bac6efead924318c9425a2e9b1b4859
- Legacy raw source: C:/Users/lycohana/AppData/Local/Temp/f5-f578-legacy-1.stl; SHA-256 f453440e823d9913a1edd6703f56bd01ce2ca8e23d0608464aea662815326ed0; 426 triangles; bbox [-50, -30, 0] to [50, 30, 50] mm; 326 boundary edges; 27 components; open.
- Rust Quick source: C:/Users/lycohana/AppData/Local/Temp/f5-matrix-two-solids-20261008-081741/native-quick/quick/r1-1791418662600182400/quick.stl; SHA-256 461e0bccb1317340e6553ea6f2bf5ef92099b929fa53c84c349f8d26ca56aeaa; 156 triangles; bbox [-50, -30, 0] to [50, 30, 50] mm; 0 boundary edges; 1 component; closed.
- Legacy->Rust weighted RMS / p50 / p95 / p99 / sampled max: 0.005587 / 0.000000 / 0.014295 / 0.029498 / 0.029498 mm.
- Rust->legacy weighted RMS / p50 / p95 / p99 / sampled max: 0.003667 / 0.000000 / 0.007605 / 0.018971 / 0.025351 mm.
- Symmetric sampled max: 0.029498 mm; surface_equivalence_passed: true.

### case-2: matrix-through-hole

- Exact input prompt: 长方体100x60x20，然后打直径8通孔
- Source report: C:/Users/lycohana/AppData/Local/Temp/f5-mesh-f578-case-2-20261008.json
- Source report SHA-256: 9a3427d219769fb7fda6c97349136a61544eaf9e28ee1f4d217d734d6fe420a0
- Legacy raw source: C:/Users/lycohana/AppData/Local/Temp/f5-f578-legacy-2.stl; SHA-256 193dc76457517fd76ccffdbce05ec8c638c43feea41be16737717342fb64b9ed; 148 triangles; bbox [-50, -30, 0] to [50, 30, 20] mm; 208 boundary edges; 32 components; open.
- Rust Quick source: C:/Users/lycohana/AppData/Local/Temp/f5-matrix-through-hole-20261008-081741/native-quick/quick/r1-1791418663679286900/quick.stl; SHA-256 732facb10c00f7a8a3bec3889ca42d0fc768819fe844e69f8ab2d61ddddfeecd; 160 triangles; bbox [-50, -30, 0] to [50, 30, 20] mm; 0 boundary edges; 1 component; closed.
- Legacy->Rust weighted RMS / p50 / p95 / p99 / sampled max: 0.002173 / 0.000000 / 0.000000 / 0.009110 / 0.030211 mm.
- Rust->legacy weighted RMS / p50 / p95 / p99 / sampled max: 0.002876 / 0.000000 / 0.000000 / 0.021313 / 0.027372 mm.
- Symmetric sampled max: 0.030211 mm; surface_equivalence_passed: true.

### round-pocket

- Exact input prompt: 圆柱直径50高40，然后圆形凹槽直径20深5
- Source report: C:/Users/lycohana/AppData/Local/Temp/f5-mesh-round-pocket-20261008.json
- Source report SHA-256: 408bded3dc322739400dc8be9f50fda67217d959f671ef296e7427cae786e838
- Legacy raw source: C:/Users/lycohana/AppData/Local/Temp/f5-round-pocket-legacy-oracle.stl; SHA-256 9b4fa67d3a65a7829320102c9c8c05357577ba1e92bf26b46cb7ddc183428dd5; 380 triangles; bbox [-25, -25, 0] to [25, 25, 40] mm; 252 boundary edges; 48 components; open.
- Rust Quick source: C:/Users/lycohana/AppData/Local/Temp/f5-round-pocket-20261008-081614/native-quick/quick/r1-1791418575865609300/quick.stl; SHA-256 f5293e07aded18da4ae500309c615dbae2549baf234f57d865125af4230a33b4; 364 triangles; bbox [-25, -25, 0] to [25, 25, 40] mm; 0 boundary edges; 1 component; closed.
- Legacy->Rust weighted RMS / p50 / p95 / p99 / sampled max: 0.013244 / 0.004394 / 0.029498 / 0.039296 / 0.039296 mm.
- Rust->legacy weighted RMS / p50 / p95 / p99 / sampled max: 0.017793 / 0.005243 / 0.035830 / 0.039333 / 0.039333 mm.
- Symmetric sampled max: 0.039333 mm; surface_equivalence_passed: true.

## Limitations

- Four deterministic barycentric interior samples per non-degenerate triangle, weighted by triangle area; point-to-triangle distance is queried against a triangle AABB BVH. This is a sampled approximation, not a continuous Hausdorff bound.
- Old raw meshes are non-closed and multi-component; Rust Quick meshes are closed single components. The topology differs.
- Signed volume of the open, multi-component legacy raw triangle soup cannot be used as a valid oriented-solid volume comparison. Raw signed-volume values/allowances in the source reports are not acceptance evidence.
- CATIA and UI behavior were not validated.
- No headless stdout JSON was present in the three corresponding TEMP run directories when checked.

## Contents

case-1/, case-2/, and round-pocket/ each contain copied legacy and Rust STL files plus comparison.json. The JSON records the full original TEMP source paths and SHA-256 values; these paths are the same information using JSON's escaped backslash syntax.
