# F5 Quick legacy compatibility acceptance

## Scope

This feature restores a deliberately bounded subset of the legacy Quick text contract in the Rust-native path. It also carries `enabled` through CADPlan/Document conversion and active-feature edits so disabled features remain saved while native geometry ignores them.

## Verified legacy oracle

The oracle is the checked-in Python implementation at `src/legacy/ai_modeler/core/parsing/nlparser.py`, reached through `nlparser.parse(text).doc.to_dict()` on 2026-10-03. `src/apps/desktop/src-tauri/tests/fixtures/quick_legacy_contract.json` stores the selected inputs and parameter-level output used by the Rust parity test.

Accepted examples:

- `法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120`: disk, center bore, and six PCD holes with the Python parser's parameters.
- `阶梯轴 直径 40 长 60, 直径 30 长 50, 直径 20 长 40`: three stacked cylinders at Z=0, 60, and 110 mm.
- `套筒 外径 80 内径 50 长 100`: preserves the public Python entrypoint's observed 25 mm hole diameter for this outer→inner→length field order, including the legacy part-library conversion behavior. The parser returns a warning explaining this compatibility quirk. `套筒` alone and `套筒 外径60 长80 内径36` follow the ordinary template path, retaining a 36 mm hole and 32 segments.
- `阶梯轴`: retains the three default segments from the old parser and marks them as derived defaults in feature sources and warnings.

Refused examples:

- `叶片 长80 通孔直径8`: the standalone legacy blade loft is migrated, but blade modifiers are not; Quick returns `unsupported` and no plan rather than silently dropping the requested hole.
- An unrelated natural-language part description with no supported primitive/template returns `unsupported` and no plan.

`叶片`、`叶片 长100 宽60` and the Python Quick `叶盘` template are migrated in [F5.24](./F5.24-quick-legacy-blade-blisk.md). The local loft remains approximate and does not claim a native CATIA multi-section loft.

## Disabled-feature behavior

- Missing `enabled` is interpreted as `true`; explicit booleans are preserved through Rust/Python CADPlan validation, CADPlan to Document conversion, native project serialization, and active feature toggle.
- Non-boolean `enabled` is rejected.
- Geometry preflight/build already skips `enabled:false` features. Serialization/fingerprints retain the status so changing feature state invalidates stale snapshots.
- Tests cover plan toggling/fingerprints, legacy conversion, Python schema/conversion/preview, and Rust template parity. The native OCCT runtime is not staged in this checkout, so no B-rep runtime build/export is claimed.

## Verification

- Rust test filter `native_quick_parser::tests`: parser tests.
- Rust golden parity filter `migrated_legacy_templates_match_python_parser_golden_contract`.
- Blade/blisk parameter, default, provenance, and fail-closed modifier contract: `legacy_blade_and_blisk_templates_match_python_parameter_and_default_contracts`; real local OCCT and AIMODEL reload results are in F5.24.
- Python CADPlan tests for disabled state round-trip, preview omission, and invalid boolean rejection.
- Frontend build attempted with `npm run build` from `src/apps/desktop`; blocked before TypeScript compilation because `vue-tsc` is absent (`'vue-tsc' is not recognized`). No package installation was attempted in this offline feature task.

## Boundaries

This is not a full port of every historical `nlparser` alias or part-library recipe. The port covers flange, stepped shaft, sleeve/tube, and existing native primitive/cut grammar. Legacy blade/blisk templates are explicitly rejected until native `loft` geometry is supported. CATIA and GUI paths are outside this acceptance.
