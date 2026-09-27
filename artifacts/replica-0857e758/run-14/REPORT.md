# 第十四轮复刻记录

## 结论

本轮未达标，结果保留为诊断候选。

- 会话：`ai-modeler-replica-0857e758-r14`；运行状态：`previewed`。
- 最终 revision：1；Agent finished：False；四视图同版通过：False。
- 模型：`moonshotai/Kimi-K2.7-Code`；推理强度：minimal；本轮 thinking budget：2048。
- 特征数：3；最终预览尺寸：[22.5, 22.5, 73.0] mm；几何核验：passed。

## 同版多模态复核

| 原图 | 预览视角 | 分数 | 证据摘要 |
| ---: | --- | ---: | --- |
| 1 | iso | 72.0 | 未通过：中部凸缘外轮廓类型 (major); 顶部圆头/锥体外形 (moderate) |
| 2 | front | 82.0 | 通过：主体与凸缘之间的水平分界面/台阶 (minor) |
| 3 | top | 55.0 | 未通过：外轮廓形状 (critical); 中心孔 (moderate); 盘面刻线 (minor) |
| 4 | bottom | 55.0 | 未通过：外轮廓形状 (major); 中心特征 (major); 同心圆台阶 (moderate); 整体比例/布局 (moderate) |

## 文件

- `run_agent.py`：从 run13 revision 7 继续的 DSH 运行脚本和本轮提示。
- `agent-events.txt`：工具调用与阶段结果；思考流只记录累计长度。
- `cad-state.json`：最近 CADPlan、预览和视觉复核历史。
- `current-preview.png`：最新单张预览；需以其对应的 preview revision 为准。
- `summary.json`：Agent 返回摘要。

## 本地候选与人工图像核对

完成工具复核后，Agent 返回 `previewed`、`finished=false`、`output_paths={}`。我通过项目现有 `_export_candidate` 导出未验收候选，保留失败复核与本地几何报告；没有绕过状态把会话标成 accepted，也没有调用 CATIA。文件位于 `output/`：

- `candidate.aimodel`：可编辑工程，3 项特征（回转主体、八边形法兰、底部盲六角槽）。
- `candidate.stl`：当前本地网格。
- `geometry-report.json`：包含当前 CADPlan、四视图评审及几何检查。
- `views/iso.png`、`front.png`、`top.png`、`bottom.png`：同一 revision 的模型视图。

同版人工对照显示，正视的主体排列、杆/法兰宽度比例和中央面/两侧面的关系与原图接近；顶视外缘确有平边但比原图看起来更规整，底视的中心六边形盲槽和环形区域大致对应原图的中心多边形及同心边界。ISO 中法兰也呈现多面体平面。外轮廓是否需要圆角化、底部环阶的确切拓扑和深度仍有图像证据歧义，因此当前作为候选，未宣称完美还原。

## 阻塞判断

本轮低分来自隔离视图评审把 top/bottom 原图描述成连续圆弧、圆形中心，并把模型清晰的平边/盲六角槽判为核心差异；iso 也将法兰判圆，而 front 82 分并认可近似多边形凸缘。`cad_finish_model` 对所有视图的 80 分门槛是硬阻断，Agent 因此回到 `cad_record_visual_review`，最终未保存其自己的成品。要让流程继续收敛，需要将跨视图投影证据用于复核/验收，并在无几何进展时保存候选和歧义，而非无限要求改到单视图自评分通过。
