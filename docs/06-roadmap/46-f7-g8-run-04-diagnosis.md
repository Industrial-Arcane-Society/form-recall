# F7.G8 真实单图冷启动 Run 04 归因

更新时间：2026-10-03。本文记录 Run04 的脱敏运行状态、结构化观察摘要、最终 CADPlan 几何参数和验收结果，并记录当前工作区在 Run04 后新增的观察契约与通用 prompt 修正；不包含模型原始回答、思考内容、provider 请求正文或凭据。

## 运行范围与结论

- 使用生产 DSH 0.2.0-rc.2、固定 Node v22.23.3、全新 CLI profile 和原始 ISO 参考图。未提供 CADPlan、人工尺寸、预设孔数或特征答案。
- model 为 deepseek-flash，请求推理强度 low。本轮 effective-profile.json 记录的 production prompt SHA-256 为 59dcc15f95ee9a4ae65fb3d8730cb068c4b20152b7b210d203dfa1182f6197eb；确认 Run04 走了生产提示词路径。Run04 后 prompt 又按本轮诊断更新，因此该指纹不能证明最新规则参加过运行。
- 输入图 SHA-256 为 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，72,309 bytes，与 Run03 原图一致。
- DSH `end_turn`，进程 exit 0；严格 runner exit 1，模型仍为未接受候选。输出目录为 `temp/acceptance/f7-g8-strict-20261003-run04-lunamid/`，完整运行数据留在本机，不纳入 Git。
- 通过：run scope、DSH 结束、trace 完整、原图校验、主图 wire、当前几何状态和产物完整性；production prompt SHA 记录与验证通过。失败：Cordis 工具审计/结果关联、孔核验、视觉复核和 finish。Run04 已完成但严格验收失败，不是待运行或可接受模型。

## 运行时观察与结构识别结果

- 主模型含图请求和成功响应各 16 次；原图传输 SHA 匹配。Run04 的 effective-profile 记录了当时加载的 production prompt SHA；该 prompt 在 Run04 后已更新，当前规则未参与这次运行。
- 结构化 inventory 为左侧孔组、中间凹面孔组和右侧孔组三组，每组各记录 3 个实例。成员相对直径比中，三个组都记录了中孔约 `0.7–0.75`，两侧孔约 `0.9–1.0`。
- CADPlan 按大—小—大写入九个独立孔：大孔 `Ø12 mm`、中孔 `Ø8 mm`。因此 Run03 中“每组全部 1.0、最终孔径全相同”的现象在 Run04 已改善；这只证明相对孔径被提取并进入 CADPlan，不代表整体识图通过。
- Run04 所用的观察契约中，三组观察项各只有一个 surface_plane_relation 枚举值，未标出可关联的支撑面 ID，也没有两组之间的成对关系。inventory 将三个孔组分别标为 distinct，无法表达“左右外板共面，中央凹面与它们平行后退”。这是运行时观察 schema 的信息缺口；没有证据表明几何转译器改写了参数。

## CADPlan 和多视图差异

- 最终计划有 11 个 feature。主体 `F1_body` 是 `length=50, width=70, height=76 mm`；三个值标为 `image_estimate`，但平面内外形比例与参考图不符。
- `F2_corner_notch` 把切除体放在 `+X/+Y` 角部，尺寸为 `14×8×60 mm`。生成的 ISO/front/top/right 视图确认它是角缺口，不是原图正面的中央贯通凹槽。
- 左孔组 L1–L3 和中央组 S1–S3 的孔轴为 Y；右孔组 R1–R3 的孔轴为 X。结果是正面只看到两列，第三列落在侧面。原图三列孔都在面向观察者的正面区域；三列虽然处在不同深度的平行面上，孔轴方向应一致。
- Rust postcheck 仍判几何有效，预览也忠实呈现该计划。当前没有证据表明 CADPlan 转译器改写了轴向或缺口位置；错误已存在于 Agent 提交的 CADPlan 参数与 feature 选择中。
- 严格关联到 15 个 native UUID；桥记录 17 次调用，ACP 13 completed、4 failed。未绑定/未审计调用及失败结果仍按门禁拒绝。
- finish 未接受，仍有 1 项 finding 未关闭；视觉覆盖不完整，孔测量门禁失败。几何有效不能替代视觉或孔验收。

## 归因与下一步

1. Run04 验证当时的生产 prompt 路径和原图 wire 有效，孔径大小等级已改善；但主体比例、支撑面关系、凹槽拓扑与孔组轴向仍错误。
2. 当前工作区已加入通用观察关系契约：每组的 surface_id、相对 primary front 的 normal_relation_to_primary、touches_part_edge，以及组间 surface_relations；touches_part_edge 与图片/ROI touches_boundary 分开。production prompt 已同步要求按支承面关系映射孔组轴向：主正面及其平行退层面使用 Y，只有有垂直侧面证据才改轴；凹槽依工件边缘接触范围重建，避免把中央退台做成角部 notch。
3. 上述 Rust 观察契约 17 项与 Python production prompt 62 项定向测试通过。这些是当前工作区测试证据；Run04 没有包含这些后续修改，修正后的真实 DSH 冷启动尚未执行，因此不能标记视觉验收通过。
4. 后续冷启动需用同一原图，无 CADPlan/尺寸预设，确认主图 wire 和 prompt 指纹后，检查各孔组实际支承面/轴向、主体比例、中央凹槽的边缘贯通状态，并由新视图独立复核。多图、纯文字、多轮编辑返修、取消、桌面 IPC 与 CATIA 分层验收仍然开放。

本 run 是单图/单轮 CLI 负样本，不代表多图、纯文字、多轮编辑、取消、桌面 WebView channel 或 CATIA 结果。
