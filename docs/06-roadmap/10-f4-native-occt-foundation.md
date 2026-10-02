# F4.1：固定 OCCT SDK 与 Rust 几何基础

## 本次完成

从官方固定标签 V7_9_3 构建 13 个 OCCT 几何 toolkit，再编译项目的 thin C ABI bridge。原有 Python wheel 为 7.9.3.1，本次官方 SDK 为 7.9.3，不能宣称二者逐位相同。源码、下载 SHA 和编译说明在 `src/native/occt-bridge/README.md`。

Rust `native_occt.rs` 动态加载 C ABI，拥有形状与网格句柄并通过 RAII 释放；形状限制在创建线程，拒绝跨 kernel 实例的句柄。当前支持长方体、圆柱、球、合并、切除、仿射变换、B-Rep 检查、包围盒、体积及三角化。它还不是完整的 CADPlan 27 类特征执行器。

加载入口 `Kernel::load_manifest` 先核验版本、双 ABI、桥 DLL、13 个 OCCT DLL 与 3 个 MSVC CRT 的实际 SHA，要求依赖同目录，拒绝缺失、重复与路径逃逸，再检查 DLL 返回版本。manifest 哈希用于检测产物不一致；发布包的签名与安装器集成尚未完成。

## 实际发现并修复

1. 运行清单中的桥 DLL digest 过期，校验器正确拒绝，重新核对产物并生成清单。
2. 合并多个实体只调用 AddArgument，OCCT 报错；改为首实体作为 argument，其余作为 tools，重叠方块体积验收通过。
3. 平移也走通用 GTransform，转换为样条后检查极慢；相似变换走专用 Transform，非均匀仿射保留 GTransform。
4. 并行三角化创建 OCCT 全局线程池，Rust FreeLibrary 时无法正常退出；改为当前几何线程内三角化，真实 CLI 正常退出。未把更快的建模截图当作进程回收成功。

## CLI 验收

Rust → C ABI → OCCT，没有 Python、LLM 或 CATIA。测试尺寸只是几何 fixture，不提供给 F7 盲测。

- 100×40×120 mm 主体，正面中央 20×8 mm 全高凹槽；3×3 九个贯穿孔，按行大—小—大。
- 一个有效、封闭实体，包围盒 100×40×120 mm，体积 429836.4628062189 mm³，解析计算误差 1.1641532182693481e-10 mm³，网格 1000 三角形。
- 重叠方块合并、球体体积、平移、旋转、非均匀缩放、无效参数与跨实例句柄拒绝均通过；CLI exit=0。
- 运行清单模块 5 项 fixture 测试；整库 75/75 复跑通过。既有工具桥取消测试此前一次间歇失败，仍需单独调查，不能宣称修复。
- 更新桥接 DLL 后，C++ CTest 1/1 通过，Rust 实际几何 probe 连续 5/5 正常退出。

在 `src/apps/desktop/src-tauri/` 执行：

```powershell
& ../../../native/occt-bridge/Write-RuntimeManifest.ps1 -RuntimeRoot ./target/f4-occt
cargo build --example native_occt_probe --offline
& ./target/debug/examples/native_occt_probe.exe ./target/f4-occt/runtime-manifest.json ./target/f4-occt/rust-fixture-report.json
```

DLL 和验收 JSON 位于本地忽略的 `target/f4-occt/`，未上传。C++ smoke 是另一组旋转布局的几何 fixture，不能替代原图的 Agent 复刻。

## 剩余工作

CADPlan 全特征执行、精确孔位测量、视图渲染和导出、运行时安装交付及取消边界未完成；F4 整体继续进行。尚未接入生产 CAD 写工具，不代表 Agent 端已摆脱 Python worker。
