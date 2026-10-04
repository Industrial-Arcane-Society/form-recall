# F4.4.1：精简 OCCT 资源打包与运行时定位

## 实现

桌面资源脚本核验 OCCT 7.9.3 / bridge ABI 1 的 manifest 与 DLL SHA256，只复制实际需要的 17 个 DLL，以及 LGPL 许可证和例外说明。暂存和替换检查路径边界；错误输入不能覆盖原有资源。

Rust 按显式 `AIMODELER_OCCT_MANIFEST`、安装资源、开发资源依次定位。找到但校验失败立即报错，不用开发路径掩盖损坏的安装资源。Agent 未安装几何运行时时仍允许只读会话；Quick 建模必须提供有效运行时。

## 验证

- Node staging 定向测试 2/2：资源替换、哈希错误和目录穿越均有覆盖。
- Rust runtime 定向测试 7/7：定位优先级、缺失资源、损坏资源不回退。
- 真实资源 staging：17 个 DLL；源 manifest SHA256 为 `39402baa4a84d3d8903e1ea97d8085e78ab047b75a568d4bb50617031a2f9649`。
- 将 PATH 缩减为 Windows 系统目录，用打包后的 manifest 运行 `native_occt_probe`：成功加载，执行融合、切除、变换、网格化。九孔凹槽 fixture 为单个有效闭合实体，包围盒 100×40×120 mm，体积误差 1.1641532182693481e-10 mm³，1000 个三角形。
- 机器报告位于被忽略的 `src/apps/desktop/src-tauri/target/f4-occt/packaged-runtime-probe.json`。

## 边界

这是资源组装和本机无开发 PATH 的 DLL 加载验证。尚未验证最终安装包、干净 Windows 机器、Agent 看图建模或 CATIA。F4 整体保持进行中。

## 2026-10-04：当前集成提交的 MSI/NSIS 编译

- 集成提交 `d41f2c62baa54f2597ff1831ee108543543b76c5` 上，`npm run tauri:build` 编译成功，生成 MSI 与 NSIS 两种安装包。
- 本机默认的 `target/f4-occt/install/` 缺少根目录许可证文件，首次构建在资源检查时停止。随后通过 `AIMODELER_OCCT_MANIFEST` 显式使用忽略目录中的源 manifest `target/package-build-input-20261004/runtime-manifest.json`；其 SHA256 为 `39402baa4a84d3d8903e1ea97d8085e78ab047b75a568d4bb50617031a2f9649`，成功校验并打包 17 个 OCCT DLL。这个本地 build input 不在 Git 中，不能当作干净 checkout 构建证据。
- 输出：`AI Modeler_0.1.0_x64_zh-CN.msi`（37,939,407 bytes，SHA256 `197cd2fcc092130586f5585f0dd5dbc72d353d634757df7dac8ccd61edf69111`）；`AI Modeler_0.1.0_x64-setup.exe`（30,355,340 bytes，SHA256 `673b34c44db00c80f7dcb8e8008774181f45199885ff2e5d8e141ce851b92`）。二者均为本地忽略产物，未提交到仓库。
- 此结果只证明当前机器具备已核验 OCCT 输入时可编译两个安装包；未安装运行、未在干净 Windows 账户或机器验收，CATIA COM 与真实 Agent 建模也未由这次打包测试覆盖。

## 2026-10-04：集成 HEAD `c518718` 的 MSI/NSIS 重编

- 在 `src/apps/desktop/` 执行 `npm run tauri:build`，指定本机忽略目录中的 `src-tauri/target/package-build-input-20261004/runtime-manifest.json`。manifest SHA256 仍为 `39402baa4a84d3d8903e1ea97d8085e78ab047b75a568d4bb50617031a2f9649`；resource staging 校验并准备 17 个 OCCT DLL 及 CATIA 按需 Python 资源，Rust release 编译和 MSI/NSIS bundle 均成功。
- 本次 Vite production build 有一个既有的 `CadViewport` chunk 大小提示（637.32 kB）；没有影响构建。
- 同一 `package-resources/app` staging 通过生产 headless smoke：以 PATH 仅含 `%SystemRoot%\System32`、清除 Python/Node 环境变量执行 `ai-modeler-headless-smoke`，返回 `status=ok`、`globalPythonRequired=false`、`globalNodeRequired=false`、无 Python/Node child；100×60×20 mm 单一有效闭合 OCCT 实体以及 AIMODEL/GLB/OBJ/STL 输出通过。该项验证 staging resource root 的 Quick→OCCT 生产调用，不是安装后的 Agent/DSH 或 CATIA 验收。
- 当前输出：`AI Modeler_0.1.0_x64_zh-CN.msi`（37,952,009 bytes，SHA256 `8d1c148523109caea2bf7eb3779b3532d6a8791a0e7cd3e7357174e9fa8212a2`）；`AI Modeler_0.1.0_x64-setup.exe`（30,354,021 bytes，SHA256 `bb77c803b1f14553b47f7bbfc5e9e7e718eacae1bdd0ba395a70dd37abe4e82a`）。两者是被忽略的本地产物，未提交到 Git。
- 该包对应当时集成 HEAD `c5187180a1541fbde8f23d59eb1cfd533c4f8f45`。构建输入包含不在 Git 中的已核验 manifest，故不构成 clean-checkout 可复现构建证据；默认 OCCT 输入仍缺失根目录许可证。没有安装测试，没有验证干净账户/机器、完整 Agent 无全局 Python/Node 冷启动或 CATIA COM。

## 2026-10-04：从 `c851967` 干净 Git worktree 验证默认打包输入

- 本次 F6 package repair worktree 起自 `c851967bf0cc3d56d1a12b6640a83d66cfdc2656`；打包时未设置 `AIMODELER_OCCT_MANIFEST`。`build_desktop_resources.mjs` 使用默认 `src/apps/desktop/src-tauri/target/f4-occt/runtime-manifest.json`；缺少或校验失败时会调用 `Build-Runtime.ps1`。显式 manifest override 若无效则立即失败，不会覆盖或回退到另一个来源。
- `Build-Runtime.ps1` 从官方 OCCT `V7_9_3` archive 构建；下载 SHA-256 为 `566a236b5a22e778dbe1c3dce6560ba12db4504bbdae0354c51dd2f4728756ee`，archive 内 peeled commit 为 `a016080bf6738d6aeae020badee4e888ad1540a5`。脚本验证 archive SHA 和 LGPL/exception 原始许可文件，再构建 13 个必需 toolkit、C ABI bridge 与 smoke；运行 CTest 后从实际 DLL 生成 SHA-256 manifest。manifest SHA-256：`046c9c7bcbe88ca97d9543c6a46db461141fc81615b3f5bc907f8b52ac67d92b`；staging 校验并复制 17 个 DLL。
- 复核边界：运行时源码与 1,848 项 C++ 构建来自新的 Git worktree 和固定官方 archive。首次 `Build-Runtime.ps1` 在完整编译/安装后因 PowerShell CTest 命令调用错误退出；修复后复用了该编译产物，CTest 1/1 通过并生成 manifest。针对“缺少默认 manifest 时调用 builder、验证产物”的单测使用隔离 fixture 通过。为遵从“不清理已编译 build/install”的要求，没有再次从空 `target/f4-occt` 端到端运行整个 builder；因此这是干净 Git 源码 worktree 的默认路径 MSI/NSIS 构建证据，不宣称已证明空 target 下单命令冷启动重编。
- 执行 `npm ci --no-audit --no-fund` 后，在 `src/apps/desktop/`、清除 `AIMODELER_OCCT_MANIFEST` 的环境中运行 `npm run tauri:build`：Vite、资源 staging、Rust release、MSI 与 NSIS 均成功。`target/package-resources/app` 有 131 个文件、6,924,338 bytes。安装包是基于上述 `c851967` 源码 worktree 的本地产物：
  - `src/apps/desktop/src-tauri/target/release/bundle/msi/AI Modeler_0.1.0_x64_zh-CN.msi`：37,972,816 bytes，SHA-256 `493504266CCC90F8EDA38C2DC415C2844610087EBD4B3F78F7D84BA08B3B0E93`
  - `src/apps/desktop/src-tauri/target/release/bundle/nsis/AI Modeler_0.1.0_x64-setup.exe`：30,366,314 bytes，SHA-256 `E868DCD3D660EBAA3E67CBF81C79DA5E00793CE97EDB1E1230649CEECBFCDBC2`
- Release `ai-modeler-headless-smoke` 对同一 `target/package-resources/app` 运行，移除 Python/Node/manifest override 环境变量并将 PATH 限为 `%SystemRoot%\System32`，结果 `status=ok`、`native_quick -> OCCT -> active_model_commit`、单个有效闭合实体、100×60×20 mm，输出 AIMODEL/GLB/OBJ/STL；`pythonChildStarted=false`、`nodeChildStarted=false`。这是 packaged resource root 的 Quick/OCCT smoke，不是安装后 UI、DSH Agent 或 CATIA 验收。
- MSI/NSIS 未安装或启动；尚未完成无全局 Node/Python 的 Agent 冷启动、真实 CATIA COM、干净用户账户验收。父任务集成 F4 改动后必须重新打包；上述哈希只标识基于 `c851967` 的本地产物，不能作为最终合并包哈希。
