# F4.2.19 CADPlan loft native execution

## Acceptance scope

Add the existing CADPlan `loft` feature to the Rust native geometry executor. Reuse the retained legacy NACA four-digit airfoil points and section placement so the feature means the same thing in the local candidate path. The builder produces a real OCCT B-Rep solid from a triangulated boundary; it does not claim an analytic `ThruSections` surface or a CATIA native loft.

## Geometry and parameter contract

- Required: `root_chord > 0`, `tip_chord > 0`, `span > 0`, and `thickness` as a dimensionless NACA thickness ratio in `0.01..=0.4`.
- Optional: `camber` in `-0.2..=0.2` (default `0.05`), `camber_pos` in `0.05..=0.95` (default `0.4`), `twist` magnitude at most `720` degrees (default `0`), nonnegative `sweep` (default `0`), `sections` integer `2..=64` (default `8`), `points` integer `8..=128` per surface (default `26`), `rotate=[X|Y|Z, angle]`, and finite `translate=[x,y,z]`.
- Native normalization converts chord/span/sweep/translate to millimetres and twist/rotate angle to degrees. `thickness`, camber, and camber position remain dimensionless.
- Each span section linearly interpolates chord and sweep, applies progressive twist about the local global-Z span axis, and samples the legacy NACA point sequence. The two concave end caps use ear clipping; side quads become triangles. Triangle count is checked against the 100,000-face OCCT input budget.
- The triangle shell is passed to `Kernel::triangle_mesh_to_solid`; the candidate is accepted only when OCCT reports `valid=1`, `solid_count=1`, `closed_solid_count=1`, and a finite positive volume. Open shells, compounds, multiple solids and zero-volume candidates are rejected.
- The native capability is `implemented_approximate`. The current CATIA map remains the legacy root-section extrusion approximation. A local OCCT success does not claim that CATIA built a multi-section loft.

## Rust and capability verification

From the repository root:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_loft
pwsh -NoProfile -ExecutionPolicy Bypass -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_capability_contract_matches_registry_preflight_and_variant_shape
pwsh -NoProfile -ExecutionPolicy Bypass -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter native_capability_registry_has_an_explicit_state_for_all_public_kinds
pwsh -NoProfile -ExecutionPolicy Bypass -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter advertised_native_parameter_limits_are_exercised_by_preflight_and_builder
pwsh -NoProfile -ExecutionPolicy Bypass -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter loft_length_angle_and_rigid_transform_units_are_normalized
```

Results: native loft mesh and refusal tests `4/4`; registry contract `1/1`; explicit all-kind state `1/1`; public parameter-boundary matrix `1/1`; cm/rad normalization `1/1`. The five invalid cases in the OCP runner are separately confirmed as schema-valid and rejected during native preflight before runtime loading.

## Actual OCCT and legacy OCP parity

Build the external-plan probe in this worktree. Keep the output under this worktree's own `src/apps/desktop/src-tauri/target`; the run below reads the already-staged OCCT runtime manifest without modifying it.

```powershell
$env:TAURI_CONFIG='{"bundle":{"resources":[]}}'
cargo build --manifest-path src/apps/desktop/src-tauri/Cargo.toml --offline --example native_plan_geometry_probe
$env:AIMODELER_OCCT_MANIFEST='C:\Users\lycohana\.codex\worktrees\f7-main-merge\AI-_-\src\apps\desktop\src-tauri\target\f4-occt\runtime-manifest.json'
$env:PYTHONDONTWRITEBYTECODE='1'
python -B src/apps/desktop/src-tauri/examples/native_loft_parity.py
```

`native_loft_parity.py` runs external CADPlans through the production Rust executor and OCCT 7.9.3, checks a one-solid closed B-Rep, and compares each result with `plan_to_document` plus the retained Python OCP `occ_kernel.build_document` path.

| Positive case | BBox maximum absolute error | Volume absolute error | Relative volume error | Native OCCT solids |
| --- | ---: | ---: | ---: | ---: |
| Legacy dimensions, mm | `1.01e-7 mm` | `11.2948 mm³` | `0.4134%` | `1 valid closed solid` |
| Physically equivalent dimensions, cm | `1.01e-7 mm` | `11.2948 mm³` | `0.4134%` | `1 valid closed solid` |
| Rad twist/rigid Y rotation, sweep and translation | `0.01101 mm` | `11.2947 mm³` | `0.4134%` | `1 valid closed solid` |

Acceptance ceilings for these fixtures are BBox `0.1 mm` and relative volume `3%`. Five negative, CADPlan-schema-valid variants are refused before OCCT: thickness `0.41`, sections `65`, points `129`, twist `721°`, and `camber_pos=0`. The generated reports and plans are kept under this worktree's ignored `target/f4-occt/loft-parity/` directory.

## Approximation limits and remaining work

- The side faces and end caps are planar triangles sewn into a faceted B-Rep. The old OCP path uses a smoothed OCCT `ThruSections` loft, so the measured approximately `0.414%` volume difference is expected for these fixtures. The stated tolerance is limited to the listed acceptance inputs; it is not a global theorem over every chord ratio, twist or sweep.
- Self-intersection, extreme taper and malformed profiles are rejected if OCCT sewing/build fails or does not produce exactly one valid closed solid. The face-count and profile limits cap resources, but do not establish a minimum feature size for manufacturing.
- CATIA export remains a root-section extrusion approximation. CATPart feature parity and clean-installer runtime acceptance are outside this feature task.
- `impeller` and `shell` remain unsupported; `shell` still lacks an explicit face-removal selection in CADPlan.
