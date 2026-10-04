# F4.2.16 CADPlan `scroll_disc` native builder acceptance

Date: 2026-10-04
Scope: native local OCCT B-Rep parity for the retained polygonal scroll-disc recipe. This is not CATIA, Agent, or image acceptance.

## Geometry contract

- All linear inputs and `center` are normalized to millimetres. `center=[cx,cy,cz]` is the geometric centre of the wall extrusion and the XY centre of the backplate; the extrusion axis is global `+Z`.
- For `R=outer_d/2`, the legacy centreline starts at `r0=0.2R`; pitch is `(R-wall_t/2-r0)/turns`. The wall profile samples the outer radial edge forward and inner edge backward, with `segments+1` samples on each edge, then extrudes symmetrically over `height`. The analytic backplate is a radius-`R` cylinder of height `max(0.5 mm, 0.35*height)`, based at `cz-height/2`; the two are fused.
- The 2D spiral is the retained sampled Archimedean polyline, not an analytic spiral surface. The approximation report bounds its chordal deviation for the selected sampling. The final candidate must be valid, contain exactly one solid and one closed solid, and have finite positive volume.
- Explicit native acceptance envelope: `outer_d >= 4 mm`, `height >= 1 mm`, `wall_t >= 0.5 mm`, `turns >= 0.5`, `segments` integer `24..=4096` (default 120), at least 24 samples per turn, positive inner spiral radius and radial travel, and no touching/overlap of neighbouring turns by the minimum normal-spacing guard. These reject conditions avoid the old helper's silent clamping and prevent malformed/undersampled native geometry. OCCT must also accept the profile and fusion; disjoint fusion and invalid/open/multi-solid results fail the whole feature/plan.
- Comparison boundaries are against the same retained sampled profile and backplate: bbox `<=1e-6 mm`, volume `<=1e-5 mm³`, tessellated area estimate relative error `<=0.6%` using OCCT mesh deflection `0.08 mm / 0.35 rad`. This does not measure error against an ideal continuous spiral.
- CATIA remains explicitly outside native parity: its current mapping makes the approximate backplate and omits the spiral wall.

## Fixtures and results

The real runner writes inputs and reports under `src/apps/desktop/src-tauri/target/f4-occt/plan-parity/`; it uses the staged `runtime-manifest.json` and `native_plan_geometry_probe.exe`, and the retained Python OCP backend from `src/legacy`.

Positive fixtures in `examples/native_geometry_parity.py`:

| Fixture | Parameters | Result |
|---|---|---|
| `scroll-disc-legacy-oracle` | `outer_d=60, height=8, wall_t=2, turns=2.5` | Passed; bbox max error `0 mm`; volume error `9.09e-12 mm³`; area estimate relative error `0.10913%`. |
| `scroll-disc-translated` | Same dimensions, `center=[12,-7,5]` | Passed; translated bounds exact within `1e-6 mm`; volume error `3.64e-12 mm³`; area estimate relative error `0.10913%`. |
| `scroll-disc-cm-unit-scaling` | `outer_d=6, height=0.8, wall_t=0.2`, center `[1.2,-0.7,0.5]`, units `cm` | Passed after mm normalization; same translated bounds; volume error `3.64e-12 mm³`; area estimate relative error `0.10913%`. |

All three native reports were valid single closed solids. The full parity runner reported **89 positive cases / 54 rejected negative cases** across the integrated matrix. Seven scroll-disc negatives were individually rejected: zero outer diameter, zero height, negative wall thickness, zero turns, segments 4097, adjacent-turn self-intersection (`wall_t=8`), and fusion with a disjoint existing stock. The Rust module tests additionally cover dimensions below acceptance floors, undersampling, excessive profile budget, non-finite centre and a collapsed inner wall.

## Reproduction

From the repository root, with the staged local OCCT runtime present:

```powershell
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_scroll_disc
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_feature_specs::tests
cargo build --manifest-path src/apps/desktop/src-tauri/Cargo.toml --offline --example native_plan_geometry_probe
python src/apps/desktop/src-tauri/examples/native_geometry_parity.py
```

Observed: module tests `2/2`, capability registry tests `9/9`, offline probe build succeeded, and parity `89/89` positive plus `54/54` negative cases passed. `cargo fmt --check` remains non-clean repository-wide due to pre-existing formatting differences in unrelated files; the new `native_scroll_disc.rs` was formatted directly. `git diff --check` passed.

## Scope and downstream risks

This closes one Rust native feature gap only. `loft`, `helix`, `impeller`, `worm`, and `shell` remain unsupported. The polygon chord approximation can vary with `segments`; tighter geometry increases profile complexity and OCCT fuse cost, while undersampled or self-touching input is rejected. Existing plan operations that expect independent additive solids may now be rejected when the scroll disc is disjoint, because the CADPlan result contract requires one fused closed body. CATIA export still omits the spiral wall, and final installer/clean-machine runtime acceptance remains outstanding.
