# F7.G8 Run37：单图冷启动诊断

## 范围

- 单图、单轮、默认参考图与默认提示词；无预置 CADPlan、尺寸或模型 seed，无重试。
- 只使用发布允许的安全摘要、构建状态和工具诊断投影。未读取 raw provider 输出、思考正文、完整几何载荷或 scratch。
- 运行源码起点为 `e32ad01`；该运行早于 main 上 ACP 生命周期投影提交 `862f5f8`，因此没有验证该新诊断字段。
- 不调用可见桌面 UI 或 CATIA；本报告不评价模型外观相似度。

## 运行结果

- 运行时间：2026-10-06，耗时 `1,719,090 ms`（28 分 39 秒）；runner exit code `1`。
- DSH transport 完成，模型状态为 `candidate`，scratch 清理状态为 `removed`。
- Node `v22.23.3`，DSH `0.2.0-rc.2`。输入原图为 `72,309` bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`；摘要记录原图未变化。
- 打包构建 gate 通过，构建 exit code `0`，耗时 `40,163 ms`。
- 安全进度计数：有效 session 更新 112、message chunks 1、thought chunks 37、工具调用/更新各 37、工具 terminal completed 29、failed 8、几何 revision 推进 8。
- strict host gates：9 项通过、4 组失败，总计 15 个失败原因；`evidence_error_count=0`。

## 失败范围

| Gate | 结果 | 安全原因代码数量 |
|---|---|---:|
| Cordis CAD tools | failed | 3 |
| ACP CAD tool results | failed | 1 |
| Visual coverage and independent review | failed | 6 |
| Finish acceptance | failed | 5 |

其余 scope、DSH session/process、trace、原图和图片 wire、current geometry、artifacts、holes 等 gates 通过。工具安全投影显示参考图检查/审查链存在 bridge 响应缺失及审计拒绝；本轮未读取自由文本来猜测底层异常原因。

## 结论与边界

Run37 产生的是候选，不是接受模型。host gate 没有接受结束状态；visual 与 finish 均失败。虽然几何和工件存在性 gates 通过，但允许发布的证据中没有能供独立多模态审查逐视图对比的图像/特征摘要，因此不能声称孔位、比例或整体形状与原图匹配。

这次结果不能用来归因于 `862f5f8`，因为运行起点 `e32ad01` 早于该提交；也不能仅凭安全原因码断言某个模型、提示词或 CAD 转译模块就是唯一根因。下一次盲测应基于更新后的源码，并保留相同单图、无 seed 条件，同时先保证 ACP/Cordis 工具终态和可审查视图能安全发布，再评价视觉质量。

## 证据位置

安全证据仅位于：

```text
src/apps/desktop/src-tauri/target/f7-run37-20261006-01/evidence/
```

允许的文件为 `summary.json`、`build-status.json`、`tool-result-diagnostics.jsonl`。本记录不复制或引用 raw scratch、模型思考正文、工具参数或完整几何数据。运行期间 scratch 已清理。
