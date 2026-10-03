# F5.4：Rust 原生活动模型 Wavefront OBJ 导出

## 范围

Quick 与 DSH Agent 都从 Rust/OCCT 三角网格生成 OBJ；活动模型的现有 OBJ 菜单入口读取当前活动 generation 的输出路径，不再依赖 Python 文档或 STL 转换。OBJ 保留三角面、顶点法线和模型毫米坐标，不附带材质文件。无效/超限网格在写入前拒绝，文件先写入同目录临时文件再发布到目标路径。

## 验收

- `cargo test --manifest-path src/apps/desktop/src-tauri/Cargo.toml --lib --offline native_model_io::tests -- --nocapture`：7/7 通过，包含 OBJ 顶点、法线、面索引、毫米数值及无效输入不覆盖原文件。
- `cargo test --manifest-path src/apps/desktop/src-tauri/Cargo.toml --lib --offline native_quick::tests -- --nocapture`：3/3 通过，含本机 staged OCCT 对明确尺寸长方体的实际 Quick 构建；AIMODEL、STL、GLB 和 OBJ 均存在。
- `cargo fmt --manifest-path src/apps/desktop/src-tauri/Cargo.toml`：通过。

## 边界

- 文件包含无索引重复三角顶点；这是有效 Wavefront OBJ，便于可靠表达 OCCT 三角网格，不做顶点合并或减面。
- CATIA 脚本/CATPart 导出、禁用特征兼容、Tauri GUI 菜单操作和 Quick→Agent 桌面端到端验收不属于此项。
- 当前 OBJ 最大输出限制为 128 MiB；过大文件明确失败，不产生部分目标文件。
