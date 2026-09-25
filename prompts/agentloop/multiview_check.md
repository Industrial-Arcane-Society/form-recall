# 阶段三：多视图检查

你会收到原始参考图、当前 CADPlan、本地网格内核生成的预览图、尺寸摘要，以及可能存在的上一轮未解决问题。实际查看每张预览；不要只看数字、凭记忆或用审查结论替代修模。

如三视图不足以判断前后错层、遮挡或贯穿关系，可调用一次 cad_get_multiview 请求扩展视图。应用会把图像附回当前对话。应用会校验修订计划并重新生成预览；不得在这里调用校验工具，也不得只写建议而不修改计划。

逐项检查：

1. 正视 XZ 外轮廓和总宽高比、左右分段宽度关系、槽/缺口轮廓。
2. 孔数、孔列/排位置、孔径等级和大—小—大次序。
3. 右视 YZ 的深度、前后位置、退台量、背面齐平关系、贯穿/遮挡关系。
4. 俯视 XY 的宽深比例、错层方向和各段前后边界。
5. 等轴测图中各实体的实际连接关系、退台、可见内壁和孔道。

对照 previous_open_findings：每个条目必须在 resolved_findings 中逐项结案，或继续作为当前未解决 issue。不得因为本轮把严重度从 high/medium 改成 low，或不再提及，就视为已解决。只有当前几何参数已改变、偏差已消失，并且 resolved_findings 指向当前 checks 中同一特征/参数的 match 检查及其 check_id，才可标记 resolved。解析不确定时保留为 open 或要求用户确认。

issues 只放当前仍未解决、需要改 CADPlan 或需要用户确认的问题；已通过的检查放入 checks，不能把正向结论伪装成 issue。所有几何 issue 必须给稳定的 issue_id，并指出 feature_ids 与至少一个 affected_parameters，后者格式为 [{"feature_id":"middle_block","path":"parameters.length"}]。修改无法从图中可靠决定时应 ask_user。

只要 issues 非空、有几何 checks.status 为 mismatch/partial、存在尚未结案的 previous finding，或置信度低于 0.6，就绝不能 accept。能根据图像修正时必须返回 decision=revise 和完整 CADPlan；无法可靠修正时返回 ask_user。revise 必须实际改变相关几何参数，不能只改来源说明或重复当前计划。

accept 前必须完整提交五项几何检查：front/outline、front/features、right/depth_setback、right/through_connectivity、top/top_boundaries。每项必须带准确的 view 与 area、category、status 和具体 evidence；所有几何检查还须标明 feature_ids 与 affected_parameters。若某项确实无法判断，先调用扩展多视图；仍不可见时才能标 unknown，并填写 unknown_reason。任何检查项缺失、questions 非空或视图覆盖不足时都不能 accept，也不能因此盲目改模型。

最多允许 3 次自动返修（首次预览之后，每次返修都要重新预览并复核；总计最多 4 次检查）。最后一次仍有 open finding 时不得接受，应用会以未完成状态停止。

严格返回 JSON：

{"decision":"accept|revise|ask_user","checks":[{"id":"front.outline","view":"front","area":"outline","category":"geometry","status":"match|mismatch|partial|unknown","item":"核对项","evidence":"具体图像与计划证据","unknown_reason":"status=unknown 时必填","feature_ids":["left_post"],"affected_parameters":[{"feature_id":"left_post","path":"parameters.length"}]}],"issues":[{"issue_id":"depth.middle_setback","view":"front|right|top|iso","category":"geometry","severity":"high|medium|low","item":"未解决偏差","evidence":"参考图与当前预览的具体差异","feature_ids":["middle_block"],"affected_parameters":[{"feature_id":"middle_block","path":"parameters.width"}]}],"resolved_findings":[{"issue_id":"depth.middle_setback","check_id":"right.middle_setback","evidence":"当前预览中的偏差已消失；引用的 match 检查说明了关联参数变化"}],"plan":null,"questions":[],"confidence":0.0}

decision=revise 时 plan 必须是完整 CADPlan；accept 时 plan 为 null。不要将估值伪称为图纸标注尺寸。
