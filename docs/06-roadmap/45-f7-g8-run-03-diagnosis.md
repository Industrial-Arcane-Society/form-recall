# F7.G8 真实单图冷启动 Run 03 归因

更新时间：2026-10-03。本报告只记录脱敏后的执行状态、尺寸参数与 gate 结果；不包含模型原始回答、思考内容、provider 请求正文或凭据。

## 运行范围与结论

- 使用固定 DSH Runtime、生产 profile 与当前 DSH 主提示词，以原始 ISO 参考图启动全新单图/单轮 CLI；没有给定 CADPlan、标注尺寸或孔数答案。
- 本地输入图片 SHA-256 为 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，72,309 bytes。
- DSH 以 `exit_code=0`、`end_turn` 结束；transport 为 completed。严格 runner 退出 1，整体 **failed**，模型状态为 candidate。
- run ID：`native-agent-coldstart-1c3fac9f-8aa6-473b-ad4d-77b7290c4324`。
- 完整机器证据在本机 `temp/acceptance/f7-g8-strict-20261003-134516-a29523ba0ab6462bacdd5677e3d272f3/`；该运行目录不提交到 Git。

## 证据链与门禁

- 通过：run scope、DSH 正常结束、轨迹完整、原图不可变、主模型图像 wire、当前几何状态和产物一致性。
- `main_image_wire` 通过：观察到 34 个带正确原图 SHA/bytes 并关联成功响应的主 DSH 图像请求。由此确认本轮主模型请求收到了原图；这不证明模型准确理解了图像。
- `cordis_cad_tools` 与 `acp_cad_tool_results` 失败。安全调用摘要为 46 次 ACP invocation、44 次 Rust bridge call；旧的参数摘要配对方式得到 36 个唯一配对、8 个歧义、2 个 ACP-only unmatched。native UUID 仅有 39 个被严格绑定。不能把缺失或失败的调用算作成功。
- ACP 终态包含 34 completed、12 failed。若干 CAD 工具调用确实失败或没有配对完成事件，因此这不只是审查脚本的歧义。
- `holes`、`visual`、`finish` 失败：没有完整孔测量和 backend verified；视觉 coverage 是 partial、没有独立可接受 review pair；当前 finish 未接受。

## 候选几何与图像对照

Rust postcheck revision 27 有 11 个 feature；几何状态 valid。主体 feature 是 `X=45, Y=51, Z=60 mm` 的 box，中间切槽约为 `12 × 4.001 × 60 mm`，并有九个单独的 Y 轴贯穿孔 feature。

CADPlan 和 front PNG 显示九孔都被建成 `Ø8 mm`。参考图中每列上/下孔明显大于中孔，所以当前候选漏掉了大—小—大的孔径关系。CADPlan 有独立孔 feature，可以逐孔表达直径；这次计划实际记录的直径本身全为 8 mm，因此没有证据表明转译器把不同孔径改成了相同值。

原图 ISO 顶面的两条主轴投影长度粗估约为 2.6:1；这是图像比例线索，不是制造标注。候选主体的两条水平尺寸为 51:45，约 1.13:1，实物看起来接近立方体。独立查看原图、候选 ISO 与 front 视图也确认两者外形比例明显不符。槽宽相对候选主体偏大；斜投影和视角限制下，这里只记作定性差异，不推导绝对尺寸。

本轮结构化 inventory 已记录左右及中间三组成员位置，但三个成员组的 `relative_diameter_ratio` 全为 1.0，成员相对大小字段没有给出可用分级。新增成员 schema 因而没有让本轮视觉模型正确识别孔径差异；主模型也没有用原图重估后写入不同的 `image_estimate`。

最终视觉摘要：inventory valid；required evidence 5；supported 0；mismatch 3；missing 5；comparable review pairs 0。共有 6 项 finding 未关闭。模型虽生成有效几何和多视图 PNG，但没有形成新版视图与原图之间可接受的完整视觉复核。

## 归因

1. **原图传输不是本轮失效点。** 主 DSH provider wire 请求与本地参考 SHA 绑定，且拿到成功响应。
2. **本轮主要几何问题出在视觉比例识别和计划选择。** Agent 能建立三列九孔和中间凹槽，但孔成员比值全填 1，主体两轴选成近等长。随后转换器按 CADPlan 原值构建，没证据显示它篡改了这些参数。
3. **独立复核没有完成闭环。** 视觉只有 partial coverage、0 个可比较 pair，6 个 finding 保持未关闭；严格 finish 正确拒绝了候选。
4. **工具审计既有实际错误也有匹配歧义。** 参数哈希无法单独区分顺序调用的相同工具/参数；但 2 个 ACP-only 调用、native 工具失败和 ACP failed 仍是本轮真实问题，不能靠修改统计规则放过。

## 下一步

- 已在提交 `eb98c99` 将无尺寸主体轴比、逐成员孔径比例与冲突 ROI 复读规则写入生产 DSH system prompt，并通过 `_prompt()` 路径测试。Run04 仍需核验模型是否按新提示词执行。
- 已在提交 `9ee6eb7` 更新相同 tool/argument hash 的 ACP 与 bridge 调用关联：只在完整时间区间和 scope 可双向唯一匹配时消歧；无终态、重叠、foreign scope 与调用失败继续 fail closed。该逻辑仍需用新 run 复核。
- 修改后以同一原始图做新的冷启动。只有新模型比例改善并由新版视图证据关闭差异，才算解决本轮图像问题。
- 本 run 只验单图/单轮链路；多图、纯文字、多轮编辑/返修、取消、桌面事件交付和 CATIA 仍须各自验收。
