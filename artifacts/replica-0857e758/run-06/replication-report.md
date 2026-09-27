# 第六轮复刻记录：单视图输入未隔离，复核结果互相矛盾

## 结论

本轮从第五轮 CADPlan 继续，临时将推理强度设为 low；没有修改应用的全局推理配置。Agent 能生成三视图、调用独立多模态复核并增量修改，但最终候选未通过：revision 24 尚未完成，未保存 AIMODEL/STL，最新三视图复核均未达到验收要求。

## 运行结果

- 输入为 4 张参考图拼成的单张标号拼版。独立复核每次只发 1 张模型视图，却仍把全部 4 张原图一起发送。
- Agent 最后保留 4 个特征：`hex_flange`、`body_revolve`、`flange_band`、`top_recess`。其中圆柱 `flange_band` 与参考图的六角法兰不符；运行中还曾加入并删除 `test_cyl`、`tri_test` 诊断特征。
- 24 次几何 revision 中，法兰连接与外形反复变化。最新正视图比早期版本连贯，但等轴测仍可见圆柱环带遮住六角结构；局部几何检查只报告网格面数、包围盒、退化面等，不检查组件连通性。
- 复核记录：revision 0 的 iso/front/top 均 45；revision 16/17 为 35；revision 19 的 front 为 82，但同一条证据明确写出“六角法兰缺失”；revision 20 为 iso 72、top 35；revision 23 为 front/iso 72、top 45。分数和逐项差异不一致，不能把 80 分阈值单独当作完成证据。
- 最新计划未完成、`output_paths` 为空。中断前的 CADPlan、事件日志、拼版和预览均保存在本目录。

## 已确认的工作流根因

提示词要求每次复核只比较匹配的原图和模型视图，但 `cad_review_reference_similarity` 实际将所有参考图与一张模型视图放入同一请求，要求审查模型自行识别视角。结果把不同投影混为一谈，例如要求 top 预览显示正视下的圆柱柄，并给出彼此矛盾的结构判断。这会误导 Agent 添加或删除错误特征。

下一步修复为：`cad_get_state` 暴露编号与文件名；`cad_review_reference_similarity` 强制 Agent 选一个 `reference_image_index`，审查器每次只接收这一张原图和对应视角预览；完成门槛检查本轮每张原始参考图是否均已单独复核。
