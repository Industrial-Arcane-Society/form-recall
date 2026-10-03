# F7.G8：冷启动真实原图盲测 01

## 范围与运行链路

2026-10-03 使用 production-profile CLI、新建 DSH session、当前应用 provider/model/reasoning 配置和原生 Cordis CAD 工具执行一次冷启动单图建模。没有预置 CADPlan、毫米尺寸或孔数答案；原始图片作为唯一参考图发送。DSH/runtime 版本为 `0.2.0-rc.2`，当时活动模型为 SiliconFlow `moonshotai/Kimi-K2.7-Code`，reasoning 设为 `low`。runner 以 `status_scope=ACP_and_original_image_transport_only_not_model_acceptance` 明确区分传输完成和模型通过。

- 原图 PNG：72,309 字节，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`；first provider request 的原图 source/transport SHA 一致。
- DSH 进程正常退出，ACP/传输 runner 状态为 `completed` / exit 0。
- 会话实际调用参考图检查、建模、预览、参考图视觉复核和 finish 工具；CATIA 未启动。
- 可复跑证据归档在 `temp/acceptance/f7-production-image-20261003-run01/`。其中 session/profile 运行目录可能含本机配置，不加入 Git；公开记录只摘录安全摘要。

## 候选结果与拒收理由

OCCT 成功生成一个 `40 × 24 × 36 mm` 的有效闭合实体，局部几何检查通过。但 CADPlan 只有三个孔特征，轴向均为 X，中心线为 `(0,0,30)`、`(0,0,24)`、`(0,0,18) mm`，直径均为 `4.5 mm`。这不是参考图所需的左、中、右三列各三孔；最终正视图没有孔，等轴预览没有呈现目标的三列孔布局。几何计量只记录 3 个物理孔实例，`hole_measurement.complete=false`。

review 请求确实把原图和 ISO 预览一起传给视觉模型，模型返回 `view_comparable=true`，但响应声称预览左侧外壁已有三个孔，并总结“9 个可见圆孔”，与预览/正视图不符。返回的 review 项 `evidence_id` 都为空字符串，报告为 unmapped；尽管模型报告了 covered ID，宿主未采纳覆盖，`covered_evidence_ids=[]`、有效配对 `independent_review_pair_count=0`，最终 review 为 `partial`。这次运行也尝试 `cad_add_hole_array`，但工具日志显示调用前后 revision 都为 5；当前归档未记录该调用的参数与工具返回值，尚不能区分参数校验失败、无效数组或其他拒绝原因。

最终会话状态为 `model_status=candidate`、`accepted=false`、`finish_state=candidate_holes_unverified`。运行的 exit 0 只证明 ACP、原图传输和工具执行流程结束，**本次建模验收失败**。不能用 Agent 的最终文字陈述或相似度分数覆盖可见模型、B-Rep 计量和宿主 gate 的拒收结论。

候选 `candidate.aimodel`、STL、GLB、几何报告及单视图位于上述目录的 `state/native-output/r5-g4-a082a29e47e51cc28c79cfb72f139e24407ca627fd011587402e3de92b7111b8-1790960352994348100/`；正视图与等轴视图均可用于对照。运行元数据记录的基线提交为 `9724ec2cabe45a2cdeabddaf588c81820c477818`，运行时工作树包含并行功能改动，故此记录不冒充 clean-tree release 测试。

## 接下来的修复/验收

1. 修正 3×3 孔布局的视觉规划与方向语义，确保模型按几何特征数表达九个孔，而非把三条横向孔误当成正面九孔布局。
2. 将每项视觉判断绑定到可验证的 observation evidence ID；模型不能声称某面有孔而不给出与该视图匹配的 evidence mapping。正视图和等轴视图分别检查可见孔列及朝向；若复核和原始 observation 冲突，触发 F7.G5 显式 conflict refresh。
3. 原图盲测冷启动重跑，保存每版 plan、几何、正视与等轴视图、review response、finding 关闭依据；仅当宿主 gate 接受才记通过。
4. 后续再覆盖多图、文字、多轮续话、取消/恢复、空白目录安装和 CATIA 独立导出验收。
