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
