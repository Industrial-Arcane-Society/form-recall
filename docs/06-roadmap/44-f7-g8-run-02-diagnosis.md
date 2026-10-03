# F7.G8 真实单图冷启动 Run 02 归因

更新时间：2026-10-03。本文只记录去标识化运行状态、工具审计和验收门禁字段，不包含模型回答、思考内容、提示词或 provider 请求正文。

## 结果

- DSH 进程正常结束：`exit_code=0`，`stop_reason=end_turn`。
- 最终为 revision 29 的候选，宿主未接受完成；严格 runner 以 `exit 1` 结束。
- 当前几何签名及输出产物完整性通过。此次结果不是 CATIA 验收。
- 总体结论为失败候选，不能将它记录为已复刻或验收通过。

## 主模型图像 wire 证据

- 37 个主模型请求边界事件带有与原图相同的 SHA，但捕获层均为 `dsh_llm_stream_boundary`，`provider_wire_observed=false`。
- 没有满足 strict gate 要求的 `node_global_fetch` 主模型请求及其成功响应关联。
- 同一轮有 8 个独立 Rust 原生视觉请求，状态均为 HTTP 200，JSON 解析和完成状态有效；它们不是主 DSH 模型请求，不能替代主模型 wire 证据。
- 因此门禁说明“主模型 wire 证据缺失”，不能据此断言原图未发送或 provider 请求失败。

## Cordis 工具调用关联

- 有 4 次调用同时具备已注册 schema、认证 scope、Rust 审计成功和 bridge 成功，但 ACP 事件未出现对应的 `completed`。旧 runner 只以 ACP completed 状态绑定执行记录，因而没有认出这些成功调用。
- 另有 4 次真实 Rust 工具执行失败，包含预览 generation 过期、施工里程碑 revision 不一致和两次删除回退保护拒绝。因此本轮工具门禁不能简单改成忽略所有失败。
- 预览与施工里程碑错误后来成功重试，但 strict 汇总仍保留历史失败；需区分已恢复错误和最终未恢复错误，并保留全部审计记录。

## 模型候选的实际验收缺口

- 安全提取的最终 CADPlan 是 `110 × 45 × 120 mm` 主体，9 个贯穿孔为 3×3 分布；孔中心比例约为 X=`±32.5/110`、Z=`±35/120`，和参考图按像素归一化估算的中心布局接近。但 9 个孔都用 `12 mm` 直径，未表达同列大—小—大变化；参考图同方向投影中大/小孔直径比例约为 `46/31 ≈ 1.48`，只作为图像比例估算。
- CADPlan 将九个孔保存为独立 feature，后端可分别设置直径，因此转译器能表达该大小变化。失败处是九孔最终都用了 `12 mm`；`sources.diameter` 虽为 `image_estimate`，证据却只有每列孔数和统一约 12 mm，没有逐排相对大小依据。
- 中间凹槽由 `30 × 8.001 × 102 mm` 的盒体切除，位于约定的正面 `+Y`。宽度比例为 `30/110 ≈ 27.3%`；参考正视图中央带约 `68/322 ≈ 21.1%`，所以模型中段相对偏宽。Z 范围为 `[-60, 42]`，主体范围是 `[-60, 60]`，顶部留下 18 mm 连接料；观察 inventory 记录中央开口上下贯通可见，因此该候选的槽高与原图证据冲突。CADPlan 来源说明显示 Agent 有意保留连续顶料；最终等轴预览里中间上孔仅部分显露，与参考图中完整圆孔不一致。
- 当前观察结果按每列一个 evidence group 记录 3 个孔，description 只记录了颜色和数量，组级 `relative_size_group` 为 `unknown`；没有表达同一列上、中、下孔的各自大小。`cad_get_state` 保留完整 inventory，信息丢失发生在 observation 的组级提示/归一化契约，而非 state summary 删除字段。提交 `1fbfaf5` 已加入成员级位置/大小证据字段；Run03 中所有成员比值仍返回 1.0，说明 schema 扩展本身没有改善视觉判断。提交 `eb98c99` 又把无量纲轴比、逐孔轮廓比较和冲突 ROI 重读要求加入实际加载的 DSH 提示词；仍须用新冷启动验证模型行为。
- 最终孔实例为 9，但 `hole_measurement.complete=false`、`geometry_backend_verified=false`。
- 视觉覆盖为 `partial`：required evidence 6、supported evidence 0、mismatch evidence 5、missing required evidence 6；当前独立可接受 review pair 数为 0。
- 6 个 finding 仍为 `awaiting_verification`，最后绑定到旧 revision 11 或 27。
- `cad_finish_model` 返回候选而非接受，结束状态为 `candidate_holes_unverified`，`accepted=false`。
- 即使修正前两节的观测和事件关联缺口，本轮候选仍会因几何计量与视觉复核未完成而失败。

## 后续动作

1. `000ee1d` 已让冷启动 runner 将 provider route identity 传给 DSH，并在 provider fetch 层采集含图请求的字节长度/SHA、请求关联、HTTP 状态和响应解析状态；不存令牌、回答、思考或原始请求体。
2. `f105e8f` 已按 tool name 与规范化参数 SHA-256 将 ACP invocation 和 bridge 调用一对一关联，再以 native UUID 精确匹配 Rust audit。旧 run 没有参数摘要，无法追溯补做该关联；新 run 才能验证接线，失败仍须 fail closed。
3. 扩展 observation 以表达组内实例的相对大小/位置，并在主Agent决策中使用这些 evidence；新 run 保留近似正确的主体/孔中心比例，修正孔径等级、中段宽度和凹槽贯通高度，证明孔的后端测量、单视图完整覆盖、finding 关闭和 finish 接受；否则如实保留失败候选。

安全证据位于本机 `temp/acceptance/` 的对应 run 目录，目录中的原始运行数据不随本报告提交。
