# F7.G8：冷启动真实原图盲测 01

## 范围与运行链路

2026-10-03 使用 production-profile CLI、新建 DSH session、当前应用 provider/model/reasoning 配置和原生 Cordis CAD 工具执行一次冷启动单图建模。没有预置 CADPlan、毫米尺寸或孔数答案；原始图片作为唯一参考图发送。DSH/runtime 版本为 `0.2.0-rc.2`，当时活动模型为 SiliconFlow `moonshotai/Kimi-K2.7-Code`，reasoning 设为 `low`。runner 以 `status_scope=ACP_and_original_image_transport_only_not_model_acceptance` 明确区分传输完成和模型通过。

- 原图 PNG：72,309 字节，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`；first provider request 的原图 source/transport SHA 一致。
- DSH 进程正常退出，ACP/传输 runner 状态为 `completed` / exit 0。
- 会话实际调用参考图检查、建模、预览、参考图视觉复核和 finish 工具；CATIA 未启动。
- 可复跑证据归档在 `temp/acceptance/f7-production-image-20261003-run01/`。其中 session/profile 运行目录可能含本机配置，不加入 Git；公开记录只摘录安全摘要。

## 候选结果与拒收理由

OCCT 成功生成一个 `40 × 24 × 36 mm` 的有效闭合实体，局部几何检查通过。但 CADPlan 只有三个孔特征，轴向均为 X，中心线为 `(0,0,30)`、`(0,0,24)`、`(0,0,18) mm`，直径均为 `4.5 mm`。这不是参考图所需的左、中、右三列各三孔；最终正视图没有孔，等轴预览只在右侧板和凹槽内壁显示孔，左侧板缺孔。几何证据只有 3 个物理孔实例，`hole_measurement.complete=false`。

最终会话状态为 `model_status=candidate`、`accepted=false`、`finish_state=candidate_holes_unverified`。review 汇总为 `partial`：一组视觉 review 不可比较，`independent_review_pair_count=0`，参考结构 coverage 不完整。运行的 exit 0 只证明 ACP、图片传输和工具执行流程结束，**本次建模验收失败**。不能用 Agent 的最终文字陈述覆盖可见模型、B-Rep 计量和宿主 gate 的拒收结论。

候选 `candidate.aimodel`、STL、GLB、几何报告及单视图位于上述目录的 `state/native-output/r5-g4-a082a29e47e51cc28c79cfb72f139e24407ca627fd011587402e3de92b7111b8-1790960352994348100/`；正视图与等轴视图均可用于对照。运行元数据记录的基线提交为 `9724ec2cabe45a2cdeabddaf588c81820c477818`，运行时工作树包含并行功能改动，故此记录不冒充 clean-tree release 测试。

## 接下来的修复/验收

1. 修正 3×3 孔布局的视觉规划与方向语义，确保模型按几何特征数表达九个孔，而非把一条横向孔误当成正面九孔布局。
2. 审查必须选取可比较的前视图及等轴视图，得到宿主认可的 evidence pair；若结果不一致，触发 F7.G5 显式 conflict refresh。
3. 原图盲测冷启动重跑，保存每版 plan、几何、正视与等轴视图、review response、finding 关闭依据；仅当宿主 gate 接受才记通过。
4. 后续再覆盖多图、文字、多轮续话、取消/恢复、空白目录安装和 CATIA 独立导出验收。
