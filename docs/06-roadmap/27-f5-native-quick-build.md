# F5.3：Quick 原生建模首个闭环

## 范围

Quick 页的明确尺寸描述直接调用 Rust `quick_build`：原生解析、CADPlan 校验、OCCT 建模、GLB 交互网格、AIMODEL 与 binary STL 输出，返回桌面 DocumentState。耗时建模在阻塞工作线程运行。

解析入口覆盖明确尺寸的基础体和切除，不能将本项视为旧 Quick 全部语法迁移完成。原有未跟踪 `quick_parser.rs` 保留，新模块使用独立 `native_quick_parser.rs`。

## 验证

- Rust Quick 定向测试 3/3，包含本机实际 OCCT 100×60×20 mm 长方体的原生输出；未安装 runtime 时该几何案例会跳过，本机已有有效 runtime，实际执行通过。
- 原生解析器定向测试 7/7。
- Vue TypeScript 检查与 Vite build 通过；现有大 chunk 提示仍存在。

## 尚未完成

没有真实桌面视觉验收。当前 Agent、Quick 与遗留 Python 文档不是统一活动状态；参数编辑、工程打开/保存、菜单导出需要 F7.G2 接线，否则可能仍操作遗留文档。F5、F6 总体保持进行中，不能将原生创建成功等同完整工作区迁移。
