# Legacy Quick C300 单槽辐条式 V 带轮 oracle

固定输入：`V带轮 基准直径300 槽数1 槽型C 内孔20 宽度180`。通过旧 Python `Parser().parse()` → `Document.build()` 实际生成并导出 binary STL；同一 Document 另行尝试 `Document.build_brep()`。运行环境 CPython 3.11.9；未运行 Rust、Cargo、CATIA 或 UI。

- Parser: `ok=true`、识别 1 条指令、无 warnings/questions/assumptions。
- Feature: 一个 `vbelt_pulley/add`；`pitch_d=300`、`grooves=1`、`groove_type=C`、`height=180`、`outer_d=309.6`、`bore_d=20`、center `[0,0,0]`。Feature provenance 将输入项记为 `explicit`，外径为 `derived`；完整 Feature、explain、profile 点列在 `single_groove_legacy_oracle.json`。
- 旧 standard profile: 5 根辐条；轮毂直径 39 mm、轮毂长 36 mm、轮缘内径 261.85 mm、辐板厚度 15.3 mm。剖面 16 点，槽根相邻重叠为 0。
- `Document.build()` raw STL: 1,064 polygons、2,108 triangles，bbox `[-154.8,-154.8,-90,154.8,154.8,90] mm`，尺寸 `309.6×309.6×180 mm`，旧 mesh volume scalar `17,815,431.5301 mm³`。STL SHA-256 `5e2b86ed6fa986151fbcd32d95faead914d6655b0419047602194d0826156b12`。
- 独立 `Document.build_brep()` 未生成 B-Rep：`GeometryBuildError`，信息为“大直径辐条式 V 带轮尚未有原生 BREP 辐条并体实现”。所以旧 raw mesh 与旧 B-Rep oracle 是不同结果；raw volume 不是实体体积。

Artifact files:

- `capture_single_groove_oracle.py` SHA-256 `ad8368f90c1b4c3fd2e397ccc4ee683357da5e21e50485118022e0a19e665625`
- `single_groove_legacy_oracle.json` SHA-256 `cc9b1b2336b593ef4464d19333f4b706fac25143e5a8aa9910cf42f4a311605d`
- `legacy_document_build_raw_single_groove.stl` SHA-256 `5e2b86ed6fa986151fbcd32d95faead914d6655b0419047602194d0826156b12`

复跑（仓库根目录 PowerShell；写入本 artifact 子目录）：

```powershell
$env:PYTHONPATH = 'src/legacy'
$env:PYTHONDONTWRITEBYTECODE = '1'
python artifacts/f5.29-vbelt-spokes/capture_single_groove_oracle.py
```
