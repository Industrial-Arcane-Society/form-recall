# F4.3.3 最终 B-Rep 孔计量

每次孔切除保存独立的 pre-cut 形体快照，并在整个 CADPlan 最后生成的实体上核对孔位。测量读取 OCCT 圆柱面的真实半径、轴线、修剪范围和角覆盖，再以材料区间检查区分贯穿孔与盲孔。宿主只把完整 360°、半径/轴线匹配且材料证据通过的实例标记 `verified_open` 或 `verified_blind`。

后续特征若扩大孔、填孔或遮断孔壁，最终形体测量会令旧孔记录变为 `candidate_unverified`。采样射线记录仍另行保存，不能替代 B-Rep 计量。没有孔的模型由汇总门禁按无孔处理；有孔模型必须所有启用实例均通过最终计量。

## 可复现验收

在 `src/apps/desktop/src-tauri/`：

```powershell
cargo test --lib
cargo build --example native_bore_metrology_probe --example native_plan_geometry_probe
target/debug/examples/native_bore_metrology_probe.exe target/f4-occt/runtime-manifest.json target/f4-occt/native-bore-metrology-report.json
python examples/native_geometry_parity.py
```

2026-10-02 结果：crate 132/132；9 个解析孔径通过实测；盲孔深度正例及错深、错位、错径、外圆柱面负例通过；CADPlan 44 组旧内核对拍与 6 个失败样例通过；九孔最终结果全部测量；后续扩大已建孔会使其旧记录失效。报告输出在忽略的 `target/f4-occt/`。

## 边界

这是合成/确定性几何及参数对拍，不是来源图像判断、真实 DSH Agent 建模或 CATIA 验收。圆柱测量使用真实 OCCT B-Rep 几何，仍不能证明孔口倒角、沉孔或螺纹特征已经完成。
