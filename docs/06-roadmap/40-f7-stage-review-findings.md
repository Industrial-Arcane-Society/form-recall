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

## 新版关闭条件与 observation 刷新

必须是更新revision、更新几何fingerprint、更新preview SHA和pair fingerprint，且原图/source SHA、用户目标fingerprint与视角不变；exact match须引用同一evidence ID及同一受影响feature集合。observation 刷新后允许受控重新绑定：当前与历史 inventory 中必须各自有唯一、非空的结构化 `source_evidence_id`，且值一致；evidence ID 与 observation 版本仍分别记录，不跨版本替换历史记录。`observation-{index}-{description-hash}` 合成 ID、空 ID、重复 ID 或历史记录冲突均不能用于自动重绑，finding 保持 unresolved 并标记需人工确认。禁止用 item、reference/model 描述或 difference 等自由文本近似配对。

当前施工阶段完整、全部启用特征在scope内、非partial，宿主 coverage integrity且coverage verified、该evidence已covered，才可以关闭。新版 mismatch 会把旧 finding 绑定到当前 observation 并保持 open；只有之后完整、当前且匹配的几何 review 才能关闭。

只改几何不能关闭问题。同revision match、旧preview、无法安全映射的新observation、未映射、范围外、空覆盖、partial、不完整阶段、SHA/目标/视角/affected feature变化或无归属 review均不能关闭。关闭记录保存 closure binding 和新旧 evidence ID，历史 binding 保留原 observation 版本。

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
- 提交 `b921717` 增加两个否决输入：`coverage_status=partial` 和 `full_current_geometry_in_scope=false`；两者都保留 awaiting-verification finding 且不改 ledger。
- native vision domain 12项通过，含新增provider mock接线检查。
- 既有aggregate gate 7项通过。
- 离线library check通过。

## 尚未覆盖

未运行真实DSH/LLM、Tauri窗口、OCCT重建、CATIA或安装包。G4仍需要原始用户图片下的真实未施工结构/已施工错位/返修/新版复查验收，不能把本fixture计为G8质量通过。现有run01不可补写成已关闭finding历史。

无法归属的缺陷只保留unknown诊断；若模型把未来孔错误归属到body，ID校验不能识别其语义错误，仍需真实视觉及更细粒度evidence归属验收。删除受影响feature、变更用户目标或刷新observation不会自动关闭旧finding；旧问题需要后续明确的归档/目标变更策略。近似match、不同视角或PNG字节未变化不会关闭；本提交优先保留未证实问题。

## 2026-10-03：已删除特征不能被当前 review 关闭

提交 `97778c6` 增加负样例：旧 finding 所属孔特征从模型中删除后，当前 review 的完整 scope 即使返回 `match`，该 finding 仍保持未解决。静态审查未发现生产 finding 闭合绕过：review 会校验当前 revision、preview SHA 与 geometry fingerprint，并在 `native_review_findings::reconcile` 中复算 scope/coverage。

定向 Rust 测试二进制编译成功；运行 `cargo test --lib native_review_findings::tests --offline` 的测试进程时 Windows loader 返回 `0xc0000139 STATUS_ENTRYPOINT_NOT_FOUND`，因此新增测试只记为“已编译、未执行”，不记为通过。目标文件 `rustfmt --check` 与 `git diff --check` 通过。没有运行真实 DSH、GUI、CATIA 或几何重建。

## 2026-10-03 补充：manifest-aware Rust test runner 验证

提交 `e9b9f8e` 增加 Windows Rust lib test CLI runner，构建时临时配置 `TAURI_CONFIG.bundle.resources=[]`，运行前为测试 exe 加 Common Controls v6 sidecar manifest，结束后清理 manifest。它解决了此前 Cargo test exe 在 Windows loader 阶段的 `0xc0000139 STATUS_ENTRYPOINT_NOT_FOUND`。

在干净隔离 worktree（HEAD `e9b9f8e`）使用以下命令执行新增负样例：

```powershell
pwsh -NoProfile -File src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline -Filter 'native_review_findings::tests::out_of_scope_match_cannot_close_a_finding_for_a_removed_feature' -Exact
```

结果为 exit 0，`1 passed; 0 failed; 0 ignored; 242 filtered out`。旧 finding 仍为 `awaiting_verification`，`resolved=[]`。未启动 GUI、DSH 或 CATIA；该结果是宿主状态负样例，不是视觉质量验收。
