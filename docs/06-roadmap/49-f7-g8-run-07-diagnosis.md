# F7.G8 真实单图冷启动 Run 07 归因

更新时间：2026-10-03。Run07 验证 Run06 后的 preview/review 视图契约修正，并使用全新 DSH 会话、状态目录和输出目录。本文仅记录结构化运行证据，不记录模型思考、provider 原始长回复或凭据。

## 运行来源与结果

- Run ID：`native-agent-coldstart-0b4313c4-911d-4522-b6f2-260b7f966ab0`；本机完整输出为 `temp/acceptance/f7-run07-blind-1a10112e36f57521e187c28e891068e3d39982/`，未纳入 Git。
- 使用 DSH `0.2.0-rc.2` 和固定 Node `v22.23.3`，以环境变量注入 API key；provider 临时 JSON 不含 key，运行后已删除。
- Git HEAD 为 `9222bea1fea8c18e9cbbdb3c52fa76dfd13cfb0f`。runner 编译并运行当时的共享 dirty worktree；元数据记录了 `native_review_gate.rs`、`native_tool_executor.rs`、`native_vision_domain.rs` 等未提交文件。结论适用于这份运行工作树，不能把所有几何行为归因到 `9222bea` 单一提交。
- 使用 production runner、DSH `0.2.0-rc.2`、固定 Node `v22.23.3`、原始 ISO 图；未给 CADPlan、尺寸或特征答案。原图 SHA-256 为 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，运行前后不变。
- production prompt SHA-256：`2e9c4ac9f67e3e13aee018ac1b06c27168c590015ebf4cb963dd7b8f4e15aee0`；本次生成的 `state/schemas.json` SHA-256：`e763e82dd8a9405861c973d2b5d4edb595d588db4c23ab63d2371ebd7679d8d2`。
- 构建 exit 0、DSH subprocess exit 0；严格 runner exit 1。通过 run scope、DSH、trace integrity、原图、main image wire、本地几何和产物门禁；失败 Cordis CAD 工具关联、ACP CAD tool result、holes、visual 和 finish 门禁。

## 视图修复结果

`9222bea` 将 preview/review 工具的可选视图限制为执行器支持的 `iso/front/right/top/back/left/bottom`。Run07 只请求 `iso`、`iso`、`front`，三个 `cad_preview_model` 调用成功，未再请求 `three/extended`；宿主也生成了七个单视图预览。独立相似度复核成功，但只覆盖部分证据，advisory score 为 45；严格视觉门禁仍因覆盖不完整、缺少原图绑定而失败。因此这次只证明工具视图枚举修正有效，不表示模型通过复核。

## CADPlan 与视图差异

- CADPlan 主体估算为 `96×60×78 mm`。本次只建了 3 个 `Ø18.5 mm` 贯穿孔，缺少中段及另一外侧两组孔；原图可见共 9 孔，且大孔/小孔有明显区别。这些模型数值是图像估算，不是原图标注尺寸。
- 中央槽被切成近乎全厚、近乎全高的通槽：约 `59.9/60 mm` 深、`77.9/78 mm` 高。生成视图里没有应保留的清晰后退背壁和背壁孔组。
- 三个 review finding 均保持 `open`，severity 均为 `unknown`：槽内侧面及内孔不可见（关联 `center_slot`）；右侧面孔组三孔缺失（关联 `base_block`）；右侧外露面缺孔（关联 `base_block`）。施工里程碑只列出实际创建的主体/槽/三孔，没有把原图观察到的所有孔组列为待完成目标；Agent 在缺失项仍 open 时结束。

## 工具契约失败

- `cad_create_model` 首次调用在 `sources` 放入 `kind/name/operation`，被报告为 3 个 `orphan_parameter_source`；同一主体重试后成功。
- `cad_add_feature` 两次因盲槽深度超过实体轴向厚度被拒，第三次改浅后成功。
- `cad_add_hole_array` 两次把 `face_center` 放在顶层，工具要求它位于 `holes[]` 的每个孔条目内；前侧三孔调用随后成功。
- 另两组孔调用失败：`back_top` 和 `slotfloor_top` 切除没有实际减少材料，后续没有成功重试，因此计划保留单组三孔。
- `cad_update_feature` 用了未声明的 `field_updates` 字段更新宽度/Y位置，没有成功重试。

## 归因与下一步

视图枚举修正消除了 Run06 的无效视图请求；主模型依然没有把原图孔组落实到完整 CADPlan，也没有根据开放 finding 补足剩余孔组。提示/schema 当前需补足：来源键只对应数值参数；孔心坐标放在单孔条目内；数值返修使用声明的 `parameter_updates/source_updates`；阶段完成按原图观察组核对孔组成员数；中央退入面保留明显的背壁材料而不是薄皮或通槽。新的提示和 schema 定向测试完成后，必须再做隔离冷启动验证。

本 run 是单图单轮 CLI 负样本，不代表多图、纯文字、多轮编辑返修、真实 WebView IPC 或 CATIA 结果。
