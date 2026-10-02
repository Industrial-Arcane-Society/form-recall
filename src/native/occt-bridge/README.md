# OCCT C ABI bridge

This is an implementation boundary for native CAD geometry. It deliberately
exposes opaque shape handles and fixed-width C data only; OCCT C++ classes,
exceptions, STL containers, and allocation ownership never cross the ABI.

## Local OCCT source and build provenance

- Official source repository: <https://github.com/Open-Cascade-SAS/OCCT>
- Fixed official release tag: `V7_9_3`, peeled commit
  `a016080bf6738d6aeae020badee4e888ad1540a5` (annotated tag object
  `adb3db9a59488050843727f082676717187b9bf6`).
- Download URL: <https://github.com/Open-Cascade-SAS/OCCT/archive/refs/tags/V7_9_3.zip>
- Downloaded archive SHA-256:
  `566A236B5A22E778DBE1C3DCE6560BA12DB4504BBDAE0354C51DD2F4728756EE`.
- Local source, build tree, and install prefix are under
  `src/apps/desktop/src-tauri/target/f4-occt/` (ignored build output).
- The compiled bridge DLL is installed beside the OCCT runtime DLLs at
  `target/f4-occt/install/win64/vc14/bin/occt_bridge.dll`; its import library
  is at `target/f4-occt/install/win64/vc14/lib/occt_bridge.lib`, and the C
  header is at `target/f4-occt/bridge-install/include/occt_bridge.h`.
- `target/f4-occt/runtime-manifest.json` contains SHA-256 hashes for the
  bridge DLL/import library/header and all built OCCT toolkit runtime DLLs.
- The build also installs `msvcp140.dll`, `vcruntime140.dll`, and
  `vcruntime140_1.dll` next to the bridge when the matching Visual Studio
  redistributable files are available.
- Intended compiler: Visual Studio Build Tools 18.4 x64, MSVC 19.50.35726;
  CMake 4.2.1; Visual Studio-bundled Ninja 1.12.1. Configure/build must run inside
  `vcvarsall.bat x64` so MSVC standard library environment variables exist.
- Build only the required geometry toolkits: `TKernel`, `TKMath`, `TKG2d`,
  `TKG3d`, `TKGeomBase`, `TKBRep`, `TKGeomAlgo`, `TKTopAlgo`, `TKPrim`,
  `TKBO`, `TKBool`, `TKMesh`, and `TKShHealing`. Visualization, Draw,
  DataExchange, ApplicationFramework, and optional third-party integrations
  are disabled.

The official archive provenance is checked with PowerShell:

```powershell
Get-FileHash src/apps/desktop/src-tauri/target/f4-occt/occt-V7_9_3-source.zip -Algorithm SHA256
git -C src/apps/desktop/src-tauri/target/f4-occt/git-check rev-parse HEAD
```

## ABI ownership and behavior

- Every returned `OcctShape*` is a distinct owned handle; release it exactly
  once with `occt_shape_release`. Input handles are borrowed for the call.
- Output pointers are cleared before work starts where applicable. Errors
  return a status code; `occt_last_error()` returns thread-local text valid
  until the next bridge call on that thread. Every C entry catches C++ and
  OCCT exceptions.
- `OcctBounds` and `OcctMesh` use C-layout fields; release mesh allocations
  only with `occt_mesh_release`.
- Transform input is row-major affine homogeneous 4x4. It creates a new
  shape, including for non-rigid affine transforms. Rigid/uniform-scale
  matrices use `BRepBuilderAPI_Transform` to preserve analytic surfaces;
  other nonsingular affine matrices use `BRepBuilderAPI_GTransform`.
- Geometry dimensions are in millimeters and volumes in cubic millimeters,
  matching the existing CADPlan geometry kernel.
- `occt_shape_analyze` reports validity, total solid count, and closed-valid
  solid count independently; `occt_shape_is_closed_solid` is the one-solid
  convenience check.
- Tessellation returns triangle soup with one xyz and one normal triple per
  vertex. Similarity/review consumers should treat it as render data; B-Rep
  validity, closure, volume, and exact bounds remain separate outputs.
- Meshing runs without OCCT's global parallel worker pool so dynamically
  loading/unloading the bridge does not deadlock under the Windows loader lock.
  Rust shape handles stay on the thread that owns their geometry job.

After every rebuilt bridge, regenerate `runtime-manifest.json` with
`Write-RuntimeManifest.ps1 -RuntimeRoot <target/f4-occt>`. This describes actual
artifact bytes; source archive verification and release signing are separate.
Rust loads only a manifest-verified runtime and checks its reported version/ABI.

`tests/smoke.cpp` checks an analytic 100×80×20 mm box, a separate 3×3
counterbore boolean case, and the target topology fixture: one 80×12×4 mm
rectangular recess plus nine through holes with Ø10/Ø6/Ø10 columns. It checks
single-solid closure, BRep validity, exact stock bounds, analytical volume,
and tessellation. These dimensions only define a native-kernel smoke fixture;
they are not input to later image-based blind evaluation.
