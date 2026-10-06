# F7.G1：独立视觉复核图像产物

## 目标

让一次真实 DSH 冷启动运行留下足够的、受限的图像证据，供独立 reviewer 将用户原图与模型当前视图逐项比较。host gate、LLM 自评和人工/多模态 reviewer 结论分别记录，互不替代。

## 当前缺口

Run38 的 safe summary 报告 13/13 host gates 通过，并记录一次视觉 review projection；但运行完成后 scratch 已清理，发布目录只有 `summary.json`、`build-status.json` 和 `tool-result-diagnostics.jsonl`，没有可供人独立查看的 preview PNG。该次运行的 scope 明确不包含独立视觉或 CATIA 验收。

## 实施边界

- 仅在 runner 删除 scratch 前发布两张固定图：经 SHA-256 固定的原始参考图 `reference.png`，以及当前 revision、generation、geometry fingerprint 对应的 ISO PNG `model-iso.png`。
- 两张输入各自限制在已知 reference root / 本次 scratch root；解析真实路径、拒绝符号链接或 junction 逃逸；再次校验 PNG 签名、尺寸、上限和预期 SHA。
- 仅写 `visual-review/reference.png`、`visual-review/model-iso.png` 和固定字段 `manifest.json`；采用新目录/独占创建，输出不含 CADPlan、几何报告、特征参数、用户文字、思考、provider 原始日志或完整 scratch。
- 仅当模型预览是当前 revision 且状态为完整 candidate 时发布。缺失、partial、陈旧或来源不符时拒绝，不影响原有 host-gate 结果，只记录固定错误码。
- 不改验收阈值，不把 CLI gate 通过等同于视觉通过。独立 reviewer 应记录图像可见事实、结构差异、视角限制与结论；CATIA 验收单列。

## 验收

1. 定向 Node 测试覆盖合法图像发布、hash/PNG/IHDR/size 错误、根外路径和 symlink/junction、元数据缺失、partial/陈旧候选与禁止覆盖。
2. 冷启动 runner 在一次完整 DSH 运行中产生上述恰好三个视觉复核文件；manifest 中原图和模型 PNG 的 SHA、revision、generation、fingerprint 与实际文件和本轮状态一致。
3. reviewer 只读取两张 PNG 与 manifest，产出与 host gate 分开的视觉判断；此步骤通过前 F7.G1 不标记完成。单 ISO 图只支持可见面的有限判断，不能据此声称全部视角/尺寸/CATIA 验收。

## 状态

实现完成并经 Run39 验证安全导出：current revision/generation/fingerprint 绑定、runner 接线和安全 summary 状态字段均通过一次真实冷启动，成功留下恰好三份视觉复核文件。定向 Node 测试 8/8，验收 gate 测试 136/136，runner `node --check` 与 `git diff --check` 通过。

Run39 的独立两图复核发现候选中段凹腔远宽于参考，且可见孔列、排数不符；Run39 同时以 `session/prompt` RPC 错误结束，strict gates 仅 6/13 通过。因此安全导出功能完成，但 F7.G1 视觉验收仍失败，CATIA 未验。详见[Run39 诊断](./109-f7-g8-run-39-diagnosis.md)。
