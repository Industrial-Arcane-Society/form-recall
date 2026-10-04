# F4.2.20 CADPlan Impeller Native Execution

Date: 2026-10-04

Base: `8ca53d2f31984e8e441b18ad6a90594b7d379a4f` (`feat/f7-p0-completion`)
Scope: one Rust native impeller builder, bounded OCCT geometry and parity against the retained Python `Document` / OCP `occ_kernel` builder.

## Legacy parameter contract

The public CADPlan schema requires all six geometry parameters: `hub_d`, `tip_d`, `height`, `blades`, `blade_t` and `twist_deg`. `center` is optional and defaults to the origin in `Document`; `segments` is optional and the old geometry defaults to 12 section intervals.

The retained OCP path constructs a global-Z frustum hub with radii `hub_d/2` and `0.55*(hub_d/2)`, a revolved rear plate with axial thickness `max(0.5 mm, 0.28*height)`, and `blades` rectangular sections. The chord is `max(1 mm, 0.62*(tip_d/2-hub_d/2))`; the blade axial span is `min(height, blade_t)` for positive `blade_t`. Sections move radially from the hub radius through 1.35 times the radial blade length, rotate their tangent/axial coordinates by `twist_deg`, and apply the legacy radial and axial scaling before blade copies are rotated evenly around global Z.

The legacy path silently floors small dimensions, expands a too-small tip radius to at least 1.05 times the hub radius, clamps blade counts below three and uses four as the minimum section count. Rust rejects those dimension/count cases instead of silently changing sourced geometry. Rust also limits blades to 128, section intervals to 128, absolute twist to 720 degrees and total blade facets to 100,000 for bounded local construction.

## Native implementation

Rust constructs the legacy hub cone and rear plate through the OCCT C ABI. It creates each rectangular blade as a triangulated section-to-section shell, sews the triangles into an OCCT solid, then fuses the hub, plate and blades. The result is reported as approximate: the blade surface is a faceted B-Rep, not the smooth `ThruSections` surface of the OCP oracle. The builder rejects a fuse result unless OCCT reports exactly one valid closed solid with finite positive volume.

The native CADPlan unit normalizer now includes `hub_d`, `tip_d`, `blade_t` and `twist_deg`. These keys were absent from its generic conversion list; explicit cm parity exposed that defect. The conversion now matches the legacy CADPlan-to-Document conversion for these length and angle parameters.

## Geometry parity

The parity runner is [native_impeller_parity.py](../../src/apps/desktop/src-tauri/examples/native_impeller_parity.py). It runs the production `native_plan_geometry_probe` against the staged OCCT manifest, builds the equivalent `Document` with `plan_to_document`, and inspects the retained Python OCP shape.

Three positive candidates passed the one-valid-closed-solid checks and bounded comparison:

| Case | Maximum bounds error | Volume absolute error | Volume relative error |
| --- | ---: | ---: | ---: |
| Explicit mm parameters | `1.0e-7 mm` | `99.6512804 mm³` | `0.615323%` |
| Translated mm parameters | `1.0e-7 mm` | `99.6512804 mm³` | `0.615323%` |
| Equivalent cm parameters and translated center | `1.0e-7 mm` | `99.6512804 mm³` | `0.615323%` |

Acceptance limits for these fixtures are `0.1 mm` bounds error and 3% relative volume error. The parity run also rejected five cases before writing a build report: missing required `twist_deg`, a tip diameter which the legacy builder would expand, zero blade thickness, 129 blades, and 129 section intervals. Zero thickness is rejected by CADPlan validation; the other native boundary checks are enforced before OCCT execution.

Run command from the repository root (set the manifest to the staged local OCCT 7.9.3 runtime):

```powershell
$env:TAURI_CONFIG='{"bundle":{"resources":[]}}'
cargo build --offline --manifest-path src/apps/desktop/src-tauri/Cargo.toml --example native_plan_geometry_probe
$env:AIMODELER_OCCT_MANIFEST='<staged-runtime>/runtime-manifest.json'
python -B src/apps/desktop/src-tauri/examples/native_impeller_parity.py
```

## Rust checks

`test-lib.ps1 -Offline -Filter native_impeller` passed 4/4 module tests; `native_feature_specs::tests::` passed 12/12 and `native_plan_geometry::tests::` passed 23/23. The separate native capability/dispatch contract, all-kind state contract and advertised parameter-limit probes each passed 1/1. The parameter-limit probes check `blades=129` and a tip below the legacy 1.05 radius ratio. These checks are directional; the repository-wide Rust test suite was not run for this feature.

## Limits

- This is local candidate geometry parity only; CATIA export still produces the legacy hub and plate and omits twisted blades.
- Blade facets approximate the retained smooth OCP loft surface. The volume difference is measured and remains under the stated 3% fixture tolerance; it is not a claim of exact surface equality.
- Only global-Z geometry is supported. Other axes or a different blade section topology are outside this implementation.
- The 128-blade / 128-section limits and 720-degree twist limit are resource bounds, not limits of the historical CADPlan schema.
- No full-agent, visual-reference, CATIA, installer or clean-machine validation is implied by these geometry results.
