# CATIA adapter 缺失不得阻断桌面与 DSH 启动

日期：2026-10-04

## 现象与根因

用户提供的首次启动界面显示：准备 DSH Agent Runtime 时因缺少 `ai_modeler/catia_com_adapter.py` 而失败。静态检查确认，`native_bootstrap.rs` 把 CATIA adapter 与 CATIA requirements 列进核心 `REQUIRED_FILES`；`validate_resource_root()` 对该列表中的任一缺失项都会拒绝资源根。Tauri 的 runtime-status、Quick 和 DSH 安装入口都要先解析该资源根，所以 CATIA 专属文件缺失会错误地挡住整套桌面流程。

当前打包脚本 `src/legacy/scripts/build_desktop_resources.mjs` 仍将 adapter 放进 app staging，Tauri config 也映射整个 app 目录。主工作区已有 staging 中 adapter 确实缺失且其余 app 文件时间较旧，但对应的 MSI/NSIS bundle 不存在，无法据此判断用户截图中的具体安装包是旧包还是构建/安装资源偏差。此差异仍需用新构建产物核对。

## 修复

提交 `31da2d9` 将 `ai_modeler/catia_com_adapter.py` 和 `packaging/desktop-catia-requirements-win-x64.txt` 移出核心必需资源，改为独立报告 `catiaAdapterAssets`。DSH、提示词、知识库、训练语料等核心资源仍 fail-closed；缺少 adapter 只影响用户主动执行 CATIA 导出/推送时的 CATIA 路径。

## 验证

- `native_bootstrap`：5/5 通过。
- 新增缺失两项 CATIA 资源时仍可解析核心资源根的正例。
- 新增 DSH lockfile 缺失时仍拒绝核心资源根的负例。
- 前端和 G5 Rust/Vue 定向测试另见当轮集成记录；本项尚未重新构建 MSI/NSIS，也未安装运行新包。

## 后续

必须运行全量资源 staging 与 Tauri 安装包构建，检查 staging 和 MSI/NSIS 中 `app/ai_modeler/catia_com_adapter.py` 的实际存在情况；再以干净用户数据验证 DSH Runtime 安装不会因该文件缺失而失败。CATIA 真实 COM 不在本次修复验证范围内。
