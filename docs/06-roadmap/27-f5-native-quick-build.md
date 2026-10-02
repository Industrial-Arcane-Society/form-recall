# F5.3：Quick 原生建模首个闭环

## 范围

Quick 页的明确尺寸描述直接调用 Rust `quick_build`：原生解析、CADPlan 校验、OCCT 建模、GLB 交互网格、AIMODEL、binary STL 和 Wavefront OBJ 输出，返回桌面 DocumentState。耗时建模在阻塞工作线程运行。

解析入口覆盖明确尺寸的基础体和切除，不能将本项视为旧 Quick 全部语法迁移完成。原有未跟踪 `quick_parser.rs` 保留，新模块使用独立 `native_quick_parser.rs`。

## 验证

- Rust Quick 定向测试 3/3，包含本机实际 OCCT 100×60×20 mm 长方体的原生输出；未安装 runtime 时该几何案例会跳过，本机已有有效 runtime，实际执行通过。
- 原生解析器定向测试 7/7。
- Vue TypeScript 检查与 Vite build 通过；现有大 chunk 提示仍存在。

## 尚未完成

- Quick parser 仍只覆盖明确尺寸的有限原生语法，没有迁移 legacy part-library/template matcher、复杂复合语句、螺纹和阵列；缺尺寸继续请求补充。
- 参数编辑、工程打开/保存、菜单导出已接入 Rust 活动模型，但真实 Quick→Agent→编辑→保存→重载/导出桌面链路仍待端到端验收。
- 禁用特征的 CADPlan enabled 语义与旧工程兼容仍未完成；CATIA 原生导出仍未实现。

因此本记录只证明首个 Quick 创建闭环，不能表示 F5/F6 整体迁移完成。
