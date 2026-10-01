# Run 09：空白建模与 reasoning off 检查

## 结论

**未达标。** 本轮从空 CADPlan 启动 DSH Agent，第一次建立的正视候选在比例和结构上偏离参考图。Agent 根据一次 55/100 的正视复核将总体宽度收窄后，继续输出大量普通文本并连续查询 prism、box、pattern 的特征规格，没有再更新几何或复核新版。中断时停在 revision 3，未生成交付模型。

## 输入与配置

- 附件 `image-1.png` 至 `image-4.png` 与仓库参考图 SHA-256 一致。
- 图像顺序：等轴测、正视、俯视、仰视。
- 模型：`moonshotai/Kimi-K2.7-Code`。
- 请求配置：`reasoning_effort=none`；生成的 DSH runtime patch 确认 `reasoningEfforts.off` 和 SiliconFlow/Kimi `thinkingFormat=qwen` 已写入。
- 这表明关闭思考的映射已传到 DSH；但模型随后仍输出普通文本，不能把这些文本计作隐藏思考流。

## 实际过程

- 首次 CAD 工具调用在启动约 3 分 30 秒后发生。
- Agent 连续调用两次 `cad_create_model`，从而将同一主体推进到 revision 2，违反“主体只创建一次”的系统提示。
- revision 2 只有一个回转特征，包围盒为 **36 × 36 × 74 mm**；正视预览显示法兰和上部圆柱过宽。
- revision 2 的 front 单图多模态复核得分 **55/100**，一次复核请求只包含 `image-2.png` 和对应的 front 预览。
- Agent 更新同一回转特征到 revision 3，包围盒降为 **28 × 28 × 75 mm**。段长比例变得更接近参考，但宽高比仍约 0.373；参考正视图的整体轮廓宽高比约 0.31（按原图轮廓像素估算）。
- revision 3 的 front 预览生成后没有重新复核。Agent 之后反复查询 prism、box、pattern 的规格，没有创建这些特征，也没有产生其它几何更新。
- 截止中断，流桥记录 **78,352 个普通文本字符、22,859 个 text-delta 事件**；没有 `reasoning-delta`。CAD 状态为 revision 3、1 个特征、`finished=false`、`output_paths={}`，没有 AIMODEL/STL/CATPart。

## 本轮发现

1. **`reasoning_effort=off` 不等于短响应。** 思考通道确实被关闭，但 Agent 在工具之间仍产生很长的普通文本并延迟工具调用。
2. **主体创建缺少幂等保护。** 新任务中模型连续创建两次主体，第二次重置到 revision 2；系统提示本身没能防止重复创建。
3. **工具探索没有目标约束。** 在唯一需要修正的正视比例明确后，Agent 反复读取并当前任务无关的 prism、box、pattern 参数规格。
4. **更新后的视图没有闭环。** revision 3 的正视预览已经生成，但没有对 `image-2.png` 再做独立多模态复核。
5. **单图复核的解释仍有误读风险。** 复核把弹头描述成“无尖端的半球”，并把正视法兰判为六角；原图还需要结合 iso/top 的可见圆形外轮廓核对，不能照搬单条复核建议。

## 文件

- 最后保存的 [front 预览](previews/r3-g2-front.png)
- 当前 [CAD 状态](cad-state.json)
- [Agent 事件日志](agent-events.txt)
- [参考图拼版](input-reference-sheet.png)
