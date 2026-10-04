# F7.G6 Native Capability Matrix Acceptance

Date: 2026-10-04
Scope: executable parity among the public CAD feature registry, native Rust support declarations, preflight, and geometry-builder parameter guards.

## Result

The native capability contract now has an executable state for all 27 `cad_get_feature_spec` feature kinds. The test asserts the complete schema set, Rust dispatch membership, exact/approximate status, advertised variants, parameter-limit entries, and explicit unsupported status. It also runs one accepted baseline and one rejected boundary case for every registered native builder kind.

Current registry: 25 implemented kinds (11 exact local B-Rep and 14 approximate local geometry) and 2 explicitly unsupported kinds. CATIA mapping is a separate contract and is not inferred from native support.

## Capability state for the 27 public kinds

| Native status | Feature kinds | Executable check |
| --- | --- | --- |
| Implemented, exact local geometry | `box`, `cylinder`, `sphere`, `hole`, `pocket`, `prism`, `revolve`, `pattern`, `spoked_wheel`, `piston`, `camshaft` | Listed in the native dispatch set; non-empty supported variants and parameter limits; preflight boundary probe |
| Implemented, approximate local geometry | `gear`, `helical_gear`, `bevel_gear`, `socket_head_bolt`, `conrod`, `cross_joint`, `rotor`, `crankshaft`, `timing_pulley`, `vbelt_pulley`, `scroll_disc`, `helix`, `worm`, `loft` | Listed in the native dispatch set; non-empty supported variants and parameter limits; preflight or builder boundary probe |
| Not implemented in the native Rust builder | `impeller`, `shell` | Each has a schema-valid fixture. `native_execution.status` is `not_implemented`, geometry support is `unsupported`, the unsupported-variant list is non-empty, Rust dispatch excludes it, and both preflight and `build()` reject it before OCCT loading. |

The registry test asserts the total is 27, with 25 native-dispatch kinds and 2 non-dispatch kinds. The expected list is explicit, so adding/removing a schema kind without updating the native support contract fails the test.

## Parameter-boundary probes

For all 25 registered builders, the test checks that the published limit text contains the asserted bound and that a valid plan passes native preflight. It then mutates the boundary and checks the gate. Preflight-level rejections are passed through `build()` with a deliberately absent manifest; the builder must return the same preflight error before loading any runtime.

| Kind | Accepted baseline | Boundary mutation | Observed gate |
| --- | --- | --- | --- |
| `box` | `length=10, width=8, height=6` | `corner=1` | Preflight rejects the advertised unsupported rounded-corner variant. |
| `cylinder` | `radius=4, height=8` | `radius=0` | CADPlan/native preflight rejects non-positive radius. |
| `sphere` | `radius=4` | `radius=0` | CADPlan/native preflight rejects non-positive radius. |
| `hole` | `diameter=2, through=true, pos=point, point=[0,0,0]` | `diameter=0` | CADPlan/native preflight rejects non-positive bore diameter. |
| `pocket` | `length=4, width=4, depth=2` | `depth=0` | CADPlan/native preflight rejects non-positive depth. The stock-thickness rule is tested separately below. |
| `prism` | rectangle `[[0,0],[8,0],[8,6],[0,6]], height=4` | `height=0` | CADPlan/native preflight rejects non-positive extrusion height. |
| `revolve` | rectangle profile `[[2,0],[4,0],[4,6],[2,6]], angle=360` | `angle=0` | CADPlan preflight accepts the plan; the native geometry builder rejects the invalid OCCT revolution angle. Also exercised with the real staged runtime below. |
| `loft` | `root_chord=30, tip_chord=16.5, span=60, thickness=0.12, sections=8, points=26` | `thickness=0.41` | CADPlan accepts the dimensionless value; native preflight rejects the thickness-ratio limit `0.01..=0.4`. The real geometry path and OCP parity are exercised in [F4.2.19](./67-f4-native-loft.md). |
| `pattern` | `mode=linear, count=2, step=[5,0,0]`, related to a preceding box | `count=10001` | Native preflight rejects the `1..=10000` count limit; `build()` returns the same error before loading OCCT. |
| `gear` | `teeth=12, module=1, height=4` | `teeth=10001` | Native preflight rejects the `6..=10000` tooth-count limit. |
| `helical_gear` | `teeth=12, module=1, height=4` | `teeth=10001` | Native preflight rejects the `6..=10000` tooth-count limit. |
| `bevel_gear` | `teeth=20, module=1, height=4, cone_deg=30` | `teeth=1000, segments=100` | Bevel-gear preflight rejects the resulting facet-BREP estimate above 100,000 triangles. |
| `socket_head_bolt` | `shank_d=6, thread_pitch=1, length=10, head_d=10, head_h=4, hex_s=5, hex_t=2` | `hex_t=5` | CADPlan preflight accepts it; native builder rejects `hex_t > head_h`. Also exercised with the real staged runtime below. |
| `spoked_wheel` | `outer_r=20, rim_w=3, hub_r=5, height=8, spokes=4, spoke_w=3` | `spokes=513` | Native preflight rejects the 512-spoke resource limit. |
| `piston` | `diameter=20, height=20, pin_d=4, grooves=3` | `grooves=257` | Native preflight rejects the 256-groove resource limit. |
| `conrod` | `length=60, big_d=20, small_d=14, big_bore=10, small_bore=6, shank_w=8, thick=5` | `big_bore=20` | Native preflight rejects a big-end bore that is not smaller than its head diameter. |
| `cross_joint` | `span=20, journal_d=6` | `span=0` | Native preflight rejects non-positive span. |
| `rotor` | `radius=20, n=48, height=8, bore_d=0` | `n=11` | Rotor preflight rejects values below 12; accepted range is `12..=4096`. |
| `vbelt_pulley` | `pitch_d=80, grooves=1, height=20, groove_type=Y` | `grooves=65, height=1000` | CADPlan width validation is deliberately satisfied first; native preflight then rejects grooves outside `1..=64`. |
| `camshaft` | `length=80, journal_d=10, base_d=14, lift=3, lobes=2, lobe_deg=0` | `lobes=513` | Native preflight rejects the 512-lobe resource limit. |
| `crankshaft` | `length=80, main_d=10, pin_d=8, stroke=16, throws=2` | `throws=129` | Native preflight rejects throws outside `1..=128`. |
| `timing_pulley` | `pitch_d=40, teeth=12, height=10, tooth_type=XL` | `teeth=513` | Native preflight rejects teeth outside `6..=512`. |
| `scroll_disc` | `outer_d=60, height=8, wall_t=2, turns=2.5` | `wall_t=8` | Scroll-disc preflight rejects touching or self-intersecting neighboring spiral turns. |
| `helix` | `radius=8, wire_r=1, height=20, turns=3` | `height=3`, `seg_per_turn=512, wire_seg=64`, or radius `0.5` with wire radius `1.1` | Native preflight rejects turn self-contact, over-budget tessellation, and a wire radius that reaches the sampled centerline curvature radius. |
| `worm` | `radius=10, height=60, thread_r=12.5, wire_r=2.5, turns=3.75`, coaxial on global Z | `thread_r=14` or `axis=X` | Native preflight rejects radial gaps and non-Z variants. Additional schema-valid probes reject off-axis `center2`, buried crown and no axial overlap; the real OCCT fuse also rejects a valid compound with two closed solids. |

The two remaining unsupported-kind fixtures are schema-valid and include each kind's required parameters: `impeller` (`hub_d=10, tip_d=40, height=8, blades=6, blade_t=2, twist_deg=15`) and `shell` (`thickness=2`). Each reaches the native unsupported-kind gate rather than failing earlier schema validation. `loft` now has an approximate native builder; see [F4.2.19](./67-f4-native-loft.md).

## Real OCCT builder-boundary probes

These probes ran against the staged runtime manifest at `src/apps/desktop/src-tauri/target/f4-occt/runtime-manifest.json`; they are not mock-kernel tests.

1. **Zero-angle revolve:** valid rectangular radial profile, `angle=0`. CADPlan/native preflight returned `valid`; `native_plan_geometry::build` loaded the staged kernel and returned an error containing `angle` from the OCCT revolution path.
2. **Socket-head bolt socket depth:** `head_h=4`, `hex_t=5`. CADPlan/native preflight returned `valid`; the staged-kernel build reached the socket-head builder and rejected `hex_t must not exceed head_h`.
3. **Pocket depth equals stock thickness:** built a `20×20×20 mm` box, then requested a `10×10` pocket with `depth=20 mm` along Z. CADPlan/native preflight returned `valid`; the builder constructed the stock and rejected the pocket with `盲槽深度必须小于实体轴向厚度` before accepting a candidate.

These three checks are the builder-only/material-relative limits. The other invalid probes are rejected during CADPlan/native preflight, and `build()` demonstrably returns the same error before runtime loading. The loft geometry run separately verifies actual OCCT sewing and the legacy OCP comparison; see [F4.2.19](./67-f4-native-loft.md).

## Commands and results

Executed from the repository root with `-Offline` and the local staged OCCT manifest present:

| Test filter | Result |
| --- | --- |
| `native_feature_specs::tests::` | 12 passed, 0 failed |
| `native_plan_geometry::tests::` | 21 passed, 0 failed |
| `native_loft::tests::` | 4 passed, 0 failed |
| `cad_plan_validation::tests::` | 8 passed, 0 failed |

The additions are in `src/apps/desktop/src-tauri/src/native_feature_specs.rs`. They do not prove CATIA export fidelity, visual equivalence, Agent behavior, or support for the six unsupported kinds; those remain outside G6.
