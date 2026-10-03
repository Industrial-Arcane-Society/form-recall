# F5.2：原生 AIMODEL 与 STL 读写

## 实现

Rust 读写旧版 Document JSON，保留未知根字段；规范加载补齐 Document/Feature 默认字段。CADPlan 先验证，再按旧转换器的长度/角度/孔位/box.base 规则转为可编辑特征文档，旁存原始 CADPlan、sources 和 feature manifest。保存使用同目录临时文件及 rename；失败保留原目标。

binary STL 使用实际 OCCT 三角网格，检查有限值、float32 范围与三角形数量，明确过滤退化片；返回落盘 float32 网格的包围盒及有符号体积。

## 真实兼容性

- Rust 读取历史 `SDK-test.aimodel` 后回写，旧 Python load_project 的 JSON 全字段相等。Document.stats 的两项特征、[-50,-40,0,50,40,60] 包围盒、480000 mm³ 体积及 12 个三角形不变。
- Rust 的 CADPlan -> Document 输出与 Python plan_to_document(...).to_dict() 全字段相等；旧加载器读取 Rust 工程后，包围盒 [-10,-5,-4,10,5,4]、体积 1600 mm³、12 个三角形。
- 真实原生九孔/凹槽测试实体导出 1000 个三角片。按 STL 标准解码 3000 个顶点，包围盒 [-50,-20,0] 至 [50,20,120]，有符号体积 430136.960764 mm³，与 Rust 落盘统计一致。
- 发现旧 Python `_stl_vertices` 的 binary 分支每条记录仅取一个顶点；其旧包围盒在本样例恰好正确，不把旧读取器当作完整 STL 拓扑验证。
- 5 项 IO 单测通过；包括旧文档默认值、转换规则、错误输出保留既有文件。集成 library 测试 117/117 通过。

开发验收中的 Python 仅用于对拍，原生 IO 的运行不调用 Python。本模块不证明完整 Quick 迁移或 Agent 生成能力。

```powershell
# src/apps/desktop/src-tauri/
cargo test native_model_io::tests --lib
cargo run --example native_model_io_probe -- <runtime-manifest> <legacy-aimodel> <output-dir>
```
