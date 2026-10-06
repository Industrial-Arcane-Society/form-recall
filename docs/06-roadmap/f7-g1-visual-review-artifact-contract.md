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

## 2026-10-06：单图冷启动预览自动配对原图

复核 G1 主 Agent 视觉路径时发现一个确定的遗漏条件：`cad_preview_model` 的 `reference_id` 是可选字段，但旧 Host 只在模型主动传入该字段时，才把原图放进预览的 Cordis image result。对于 runner 固定使用一张原图的冷启动，即使 Host 已持有该原图，Agent 漏传参数仍只收到当前单视图，导致同一次回复中缺少原图和模型图的并排上下文。之前的配对回归只覆盖了显式 ID，不覆盖这个单图默认路径。

现由 Host 在未传 `reference_id` 且会话恰有一张登记原图时自动选择该图；读取仍经 `reference_input` 重查路径与源 SHA，输出顺序仍为原图、当前所选视图，并继续写入 comparison binding 元数据。多图会话不会猜测配对；未传 ID 时保持仅返回模型视图，显式 ID 则沿用原行为。模型 facing tool description 也已说明这条默认规则。

验证使用 runner 默认原图路径 `temp/evidence-archive/artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`（72,309 bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`）。定向 Rust 回归以测试内生成的 2×1 PNG fixture 验证唯一原图自动选择、原始字节和 ID 保持、双图歧义时不自动选择，并保留显式 ID 与错误 ID 检查；Native schema 回归 1/1，Cordis 配对输出回归 6 passed/0 failed/1 skipped，`git diff --check` 通过。完整 lib harness 的额外运行结果为 382 passed、2 failed、1 ignored；两项失败分别要求 Node executable 注入和真实 OCCT runtime，不在本次针对路径内。此 worktree 未含 pinned DSH runtime bundle，因此没有执行真实 DSH/provider 冷启动；本地回归证明 Host 选择和图片结果契约，不声称 provider 消费或视觉理解通过。原图读取为只读，没有预置 CADPlan/孔数/尺寸。

本修复关闭单原图场景中因漏传可选 ID 而导致主 Agent 单独看到模型预览的确定性缺口，但不替代 G1.2 安全包复跑、独立 reviewer 记录、真实 provider 视觉理解或 CATIA 验收。
