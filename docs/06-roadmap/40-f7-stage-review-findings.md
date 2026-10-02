# F7.G4：阶段视觉 finding 生命周期

## 本次独立 feature

原生阶段 review 现在会把归属有效的 mapped mismatch 写入宿主 finding ledger。原生几何编辑继续使用已有事务逻辑，将 open finding 置为 awaiting_verification；只有后续当前版本的独立 review 才能将其关闭。aggregate gate 继续读取同一 unresolved_findings，不新增评分通过捷径。

新增 `native_review_findings`，接入 `native_vision_domain::review`。没有修改中央 Todo、F7.G7 watchdog、F5 Quick、活动模型主逻辑或安装流程。

## 归属与绑定

- 保存 reviewer 原始 affected feature IDs；空归属或含当前 completed scope 之外 ID 的 mismatch/partial_mismatch 降为 unknown，保留 reported_status 和诊断，不能创建 finding 或贡献 covered evidence。
- out_of_scope 保留为范围外证据，不能创建或关闭 finding。ID 归属只证明当前施工 ID 成员关系，不能证明视觉模型理解了孔与特征的实际关系。
- finding 创建要求当前 revision、CADPlan geometry fingerprint、预览 generation/view/SHA、施工 scope、有效 source inventory、source SHA、observation ID/fingerprint，以及 mapped 且唯一的 evidence ID。
- finding ID由 reference、observation、evidence、view和affected feature IDs产生，跨几何 revision稳定；重复同一pair不会产生重复 finding或history。
- 保存首次revision、最后mismatch revision、引用/模型证据、差异、严重性、建议和版本绑定历史；已关闭问题再次出现会重新 open并保留历史。
- 旧缓存没有 finding_lifecycle 标记时不复用，避免迁移前未归属 review绕过新规则。

## 新版关闭条件

必须是更新revision、更新几何fingerprint、更新preview SHA和pair fingerprint，且原图/source SHA、observation ID/fingerprint、用户目标fingerprint与视角不变；exact match须引用同一evidence ID及同一受影响feature集合。当前施工阶段完整、全部启用特征在scope内、非partial，宿主 coverage integrity且coverage verified、该evidence已covered，才可以关闭。

只改几何不能关闭问题。同revision match、旧preview、新observation、未映射、范围外、空覆盖、partial、不完整阶段、目标变化或无归属 review均不能关闭。关闭记录保存closure binding，而不是仅删除问题。

## 两条 deterministic 验收

1. 未施工孔：scope只有body，review误把future hole报mismatch。宿主归属变unknown、覆盖清空、ledger无finding。provider mock接线另覆盖future ID误报与body合法缺陷两个返回。
2. 已施工错位孔：r2 mapped mismatch创建finding；真实调用Rust session Update改变孔位置并进入r3/awaiting_verification；r3当前独立review fixture exact match关闭finding。反例逐项检查旧revision、旧PNG、unmapped、out_of_scope、无覆盖、不完整scope、partial、新observation、新目标和无归属不可关闭。

测试使用固定CADPlan和mock视觉响应；PNG传输fixture是现有纯色PNG。它们验证Rust宿主状态与接线，不证明真实模型识别错位孔、不证明OCCT几何或真实图片复刻正确。

## 本次验证

在 `src/apps/desktop/src-tauri/` 执行，均退出0：

```powershell
rustfmt --edition 2024 src/native_review_findings.rs
cargo test native_review_findings::tests --lib --offline
cargo test native_vision_domain::tests --lib --offline
cargo test native_review_gate::tests --lib --offline
cargo check --lib --offline
```

- 新增生命周期4项通过；含一个fixture中的多个否决分支。
- native vision domain 12项通过，含新增provider mock接线检查。
- 既有aggregate gate 7项通过。
- 离线library check通过。

## 尚未覆盖

未运行真实DSH/LLM、Tauri窗口、OCCT重建、CATIA或安装包。G4仍需要原始用户图片下的真实未施工结构/已施工错位/返修/新版复查验收，不能把本fixture计为G8质量通过。现有run01不可补写成已关闭finding历史。

无法归属的缺陷只保留unknown诊断；若模型把未来孔错误归属到body，ID校验不能识别其语义错误，仍需真实视觉及更细粒度evidence归属验收。删除受影响feature、变更用户目标或刷新observation不会自动关闭旧finding；旧问题需要后续明确的归档/目标变更策略。近似match、不同视角或PNG字节未变化不会关闭；本提交优先保留未证实问题。
