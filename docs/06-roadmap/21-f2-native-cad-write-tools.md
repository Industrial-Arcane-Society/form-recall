# F2.5：Rust 原生 CAD 写工具

更新时间：2026-10-02。

## 结果

DSH 仍由原生 Cordis 注册建模工具，Cordis 通过 Rust Host 的本机 JSON-RPC 桥传递请求。Rust 持有 CADPlan 会话、写工具和本地 OCCT 建模调用。F2.5 完成后，Agent Host 不再创建 Python CAD tool worker；未注册或尚无 Rust 实现的工具直接报错，不回退到 Python。没有接入 MCP。

Rust 原生路由覆盖当前全部 25 个 Cordis schema：创建模型、标准件配方、添加/修改/删除特征、凹槽与孔阵列、校验、单视图预览、候选恢复、读取参考图、模型完成、施工里程碑、自述复核、知识库与网页工具、独立视觉观察和复核。测试会对 schema 与 Rust 路由逐一核对；未知工具在读取或改变 CAD 状态前拒绝。

完成路由迁移后，仓库中仍残留旧的 `dsh_tool_worker.py` 私有 JSONL 启动脚本，但已没有任何生产调用方。本次 F2 收尾删除了该死入口；保留 `dsh_tool_domain.py` 等遗留模块，因为旧 Python 测试仍直接覆盖其知识检索与复核逻辑，且 schema 迁移记录仍引用原始来源。

原生几何调用写入本地 `.aimodel`、STL、CADPlan、几何报告及七张单视图预览图。该路径只证明本地 OCCT 结果；不代表 CATIA 已启动或 CATPart 已验收。F4 安装交付尚未完成时，应用需要显式 OCCT runtime manifest；开发验收使用仓库中固定的 OCCT 7.9.3 manifest。

## 验收记录

- `cargo test --manifest-path src/apps/desktop/src-tauri/Cargo.toml --lib --offline`：140/140 通过。包括全部已注册 Cordis 工具的 Rust 路由、未知工具拒绝、会话事务、用户来源、几何与视觉门禁。
- 固定 Node `v22.23.3` 与 DSH `0.2.0-rc.2` 的真实 CLI 会话 `0c9a4e97-b76c-4392-898c-19410989456f` 实际调用了 `cad_apply_recipe`。本机几何执行器不含兼容 fallback；DSH 与 Rust stderr 均为 0，退出码均为 0。运行脚本：`src/apps/desktop/src-tauri/examples/native_cad_write_acceptance.mjs`；机器报告位于忽略目录 `src/apps/desktop/src-tauri/target/f2.5-native-write-dsh-live-04/`。
- 该调用用当前用户原文 `M5x8` 生成一项 `socket_head_bolt` 特征。两项用户来源都由 Host 绑定到当前轮次；ISO 4762 / GB/T 70.1—2008 表格提供螺距及头部尺寸。OCCT 返回一个有效闭合实体，包围盒 8.5 × 8.5 × 13.01 mm，并生成七视图及五种文件输出（AIMODEL、STL、CADPlan、几何报告、预览）。运行记录在本地忽略目录 `src/apps/desktop/src-tauri/target/f2.5-native-write-dsh-live-03/`。
- 螺杆本地 STL 的螺纹是 48 段分面近似，共 11,712 个螺纹三角面，节距 0.8 mm。几何报告标记 `approximate`。`catia_executed=false`；现有 CATIA 导出边界仍不能证明生成螺纹 CATPart。
- 直接 CLI 事务覆盖 `cad_create_model`、`cad_add_feature`、`cad_update_feature`、`cad_remove_feature`、`cad_preview_model` 与 `cad_get_state`。删除几何如果会返回已见过的 CADPlan hash，且没有当前复核 finding ID，守卫会拒绝；附带当前 finding ID 后删除与重新预览成功，最终为单一闭合实体 100 × 60 × 20 mm。输出留在忽略目录 `src/apps/desktop/src-tauri/target/f2.5-native-write-sequence/`。
- `node --check examples/native_cad_write_acceptance.mjs` 与 `cargo build --example native_read_probe --offline` 通过。

## 范围外与下一步

此次真实 DSH 调用按验收提示明确要求模型只执行一次 `cad_apply_recipe`，用于验证 DSH→Cordis→Rust→OCCT 链路；它不证明模型能自主选择正确特征，也不等同于图像理解或多模态验收。图像复刻质量和逐视图差异关闭属于 F7。CATIA COM 与 Quick 模式仍会使用遗留 Python 路径；F6 继续把 Python 收缩到 CATIA COM。
