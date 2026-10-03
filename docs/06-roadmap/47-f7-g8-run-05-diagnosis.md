# F7.G8 真实单图冷启动 Run 05 归因

更新时间：2026-10-03。本文记录当前 production prompt 与原生 CAD 工具链的单图冷启动结果。只记录可复核的运行状态、CADPlan、宿主审计和生成视图；不记录模型思考、原始 provider 回复或凭据。

## 运行范围与结果

- 使用生产 DSH `0.2.0-rc.2`、固定 Node `v22.23.3`、全新 CLI profile 和原始 ISO 参考图；没有传入 CADPlan、人工尺寸或预设特征答案。
- 输入图 SHA-256：`53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，72,309 bytes。运行前后 hash 一致，原图与主模型图片 wire 门禁通过。
- Run05 实际加载的 production prompt SHA-256：`eff320ddc00f6db8a190f269d939d3dbf1de4d0f925891538fd7859c70aac416`。之后的 schema/prompt 契约补丁不属于 Run05，必须另起冷启动验证。
- DSH `end_turn`、进程 exit 0；严格 acceptance runner exit 1，最终状态为 `candidate`。完整输出只保存在本机 `temp/acceptance/f7-g8-strict-20261003-run05-7ad1d6a7c99b4f2ca6e08ff1612a0df8/`，未纳入 Git。
- 通过：run scope、DSH、trace integrity、原图 SHA、主模型 image wire、本地几何和产物完整性。失败：CAD 工具审计/结果关联、ACP 工具结果完成、孔测量、视觉覆盖和 finish。

## 工具链失败

工具桥接审计有 34 次调用，27 次成功、7 次失败；native audit 共 35 条记录，其中失败 7 条。分类如下：

1. 两次 `cad_get_reference_image` 和一次 `cad_inspect_references` 传入越界 ROI。三次 tuple 均按 `[x,y,width,height]` 解释时满足不了 `x+width<=1` 或 `y+height<=1`。校验器正确拒绝，但工具 Schema 没明确四项语义和范围。
2. 首次 `cad_create_model` 为主体 `center=[0,0,0]` 漏掉同名 `sources.center`，provenance 校验器正确拒绝；模型随后补来源并成功创建。
3. 一次相似度复核传入旧 `preview_generation`，不属于当前 revision；模型重新生成预览后再次复核成功。
4. 一次几何修改后继续使用旧施工里程碑，宿主因 revision 已变化而拒绝；重新登记范围后继续。
5. 一次删除凹槽会回到历史 CADPlan，但传入的 finding 已处于 `awaiting_verification` 且来自更早 revision，历史几何保护正确拒绝。

上述记录没有显示并发 revision 写入。主要缺口是工具 Schema/错误信息对模型不够直接，以及模型选择了过期预览、里程碑和 finding；校验器的拒绝行为符合当前安全约束。

## 最终几何与多视图核对

- 最终 CADPlan 有一个闭合实体，主体 box 为 `90×45×130 mm`，本地 OCCT 几何报告体积 `402367.7 mm³`。这些是本轮图像估算值，不是原图尺寸标注。
- CADPlan 最终有 9 个孔实例，但 9 孔均为 `Ø16 mm`，未复现参考图可见的大—小—大孔径关系。Host 孔状态为 `candidate_holes_unverified`，`complete=false`、`backend_verified=false`；因此几何成功不能证明贯穿核验完成。
- 正视视图虽然显示 3×3 孔位，但孔径相同；顶视仍是完整平面，中央凹槽上方保留遮挡材料，没有形成参考图可见的顶部开口。等轴视图中中央退层孔组大部分被前侧凸台遮挡，只露出弧边。
- host visual gate 记录 0 个可接受对照配对和 2 个未关闭 finding；finish 未接受。CATIA 未启动，未创建或验证 CATPart。
- 模型对话中自述的孔已贯穿、复核分数上升，和宿主的孔/视觉门禁相矛盾；这些自述不构成验收证据。

## 归因与后续

Run05 证明主图确实送达 DSH 主模型，但没有证明视觉理解或模型通过。首轮主体来源遗漏导致工具拒绝，随后完成的模型仍把孔径做成等径、没有打开中央凹槽顶部；独立视觉配对也未形成，几何候选无法被接受。

Run05 后已补强 production prompt 与原生工具 Schema 对 ROI tuple、数值 provenance、revision 变化后的施工里程碑/预览顺序、当前 preview generation，以及优先更新特征和限制历史回退的说明，并增加工具/提示词契约测试。该补丁随后进入 Run06 冷启动，但 Run06 仍只生成六个等径孔，漏掉中间组三孔，且凹槽没有形成明显后退面，详见 [Run06 归因](./48-f7-g8-run-06-diagnosis.md)。Run06 后另发现 preview/review Schema 超出执行器视图能力，提交 `9222bea` 已修正；Run07 确认 Agent 不再请求不支持的视图，但 strict acceptance 仍因单组三孔、凹槽拓扑、未关闭 finding 和视觉覆盖不足而失败，详见 [Run07 归因](./49-f7-g8-run-07-diagnosis.md)。

Run05 是单图、单轮 CLI 失败样本；不代表多图、纯文字、多轮编辑、真实桌面 IPC 或 CATIA 验收。
