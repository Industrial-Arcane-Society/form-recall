# F7.G8 真实单图冷启动 Run 06 归因

更新时间：2026-10-03。本文记录 Run06 的严格冷启动验收。仅保存运行元数据、工具审计、CADPlan 摘要和生成视图结论；不保存模型思考、provider 原始长回复或凭据。

## 运行范围

- 使用生产 runner `src/apps/desktop/src-tauri/examples/native_agent_coldstart_acceptance.mjs`、DSH `0.2.0-rc.2`、固定 Node `v22.23.3`、全新 DSH_HOME/profile/state 和原始 ISO 参考图。
- 没有提供 CADPlan、人工尺寸或预设特征答案。运行前后原图 SHA-256 一致：`53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。
- Run06 使用的 production prompt SHA-256：`cb8998f2f978189bf23e8fb4ed2ba3b8762ae53e19770d2e8cd160b3570c72e2`；Schema 源文件 SHA-256：`3269577985e63e57e889fabcbc14b2921eb34fb1a0a9665c8baca4663a3beb68`；运行时生成的 `schemas.json` SHA-256：`20a93cb8b2a0e73f3ce1c8615fd852e51e6bc8a441d8aac9eeda1c43fdf9cda5`。
- Run ID：`native-agent-coldstart-f2be5757-ccce-4604-ad5f-29b18bc054e1`。DSH completed、进程 exit 0、离线构建通过（9.218 秒）；严格 runner exit 1，结果为未验收候选。
- 本机完整输出目录：`temp/acceptance/f7-g8-strict-20261003-run06-f9b71ddfe3ef4a8184f2d89f9203d042/`，未纳入 Git。CATIA 未启动。

## 门禁与几何结果

- 通过：run scope、DSH、trace integrity、原图 SHA、主模型 image wire、本地 current geometry、产物完整性。
- 失败：Cordis CAD 工具审计与调用关联、ACP CAD tool result completion、孔核验、视觉覆盖/参考绑定和 finish。最终状态 `candidate_holes_unverified`，有 3 个未关闭 finding。
- CADPlan 主体为估算的 `300×105×240 mm` 长方体；创建 6 个 `Ø24 mm` 孔，缺少中间后退面的 3 个孔，没有实现图示的孔径大小顺序。参数是本次模型的图像估算，不是原图标注尺寸。
- 中央切槽约 `10×240×30 mm`；生成的 top view 显示槽从前缘开口，但只进入约 `30/105` 的厚度。ISO/front/top 视图均未形成宽而明显的后退中段面，左右两翼基本共面。几何有效不等于目标拓扑已复现。

## 工具失败

- `cad_preview_model` 请求 `three`，Rust 预览执行器只支持 `iso/front/right/top/back/left/bottom`，调用被拒绝。
- 两次 `cad_review_reference_similarity` 因 `preview_generation` 不属于当前 revision 被拒绝。
- `cad_remove_feature` 试图回到历史 CADPlan 状态，被几何回退保护拒绝；本次 finding 不足以授权回滚。
- 一次 `cad_get_reference_image` bridge 事件标为失败，但对应 native audit 缺少记录，bridge 也没有保存错误文本。相同参数的后续两次调用成功，因此无法归因该单次失败。

## 后续修复与当前限制

Run06 后确认 `cad_preview_model` 和 `cad_review_reference_similarity` 的 Schema 曾允许 `three/extended`，超出 Rust 执行器能力。提交 `9222bea` 已将两项枚举与 production prompt 收敛到实际支持的七个单视图，并新增定向回归测试。Run07 冷启动确认 Agent 只请求受支持的视图、预览调用成功；整体严格验收仍失败，见 [Run07 归因](./49-f7-g8-run-07-diagnosis.md)。

Run06 证明原图进入主模型请求且宿主确实生成了闭合实体，但 Run05 后的 provenance、ROI、里程碑/预览和特征修复提示仍没有带来正确的孔数、孔径和中段后退面。接下来要把 Run06 留下的失败调用和 CADPlan 分开诊断，不能把 DSH `end_turn`、本地几何通过或模型自述算作视觉验收。

本 run 是单图单轮 CLI 负样本，不代表多图、纯文字、多轮编辑返修、真实 WebView IPC 或 CATIA 结果。
