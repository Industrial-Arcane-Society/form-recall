# 阶段四：相似审查

独立对照原始文字/图像和当前预览，检查多视图阶段遗漏的几何偏差。这里不仅打分：若发现可修复问题，必须返回 revise 与完整 CADPlan；应用会编辑、校验、重建预览，再重新执行多视图与相似审查。

按证据检查：外轮廓宽高比、分段宽度比、退台方向和量级、段间前后位置、特征数量与布局、孔径等级关系、厚度/深度比例及可见连接关系。CADPlan 没有颜色/材质属性时，把颜色差异单独记为 appearance，不得与几何混淆。

关键几何检查 status 只能为 match/mismatch/partial/unknown。mismatch 必须 revise（能从图像判断修正时）或 ask_user（缺少能区分方案的证据时）；几何 partial 不能当作通过，必须尝试基于图像估值修改并重新预览，或 ask_user。unknown 仅用于参考图确实不可见的属性，例如隐藏背面或无法确认的贯穿终止面，并在 remaining_differences 中披露。

若多视图阶段存在 previous_open_findings，逐项确认是否已被当前几何实质修复；不能因为分数较高或把严重度下调而忽略。当前参数未改变且对应偏差仍可见时，必须 revise。针对 issue 的修订要保留 schema_version、单位、坐标系、特征类型、来源、关系和所有已匹配的几何，只改有证据支持的参数；数值参数来源不能删除。

只有所有关键几何 checks 均为 match 或有明确依据的 unknown、没有任何未结案问题、confidence 至少 0.6 时才可 accept。similarity_score 不能覆盖 mismatch/partial，也不能单独触发 accept。

accept 前必须完整提交五项几何检查：front/outline、front/features、right/depth_setback、right/through_connectivity、top/top_boundaries。每项带准确 view、area、category、status、具体 evidence、feature_ids 与 affected_parameters。若 status=unknown，必须说明 unknown_reason。不能省略一个检查后仍依据总体印象给高分。questions 非空、几何 remaining_differences 非空、检查项缺失或证据不足时都不能 accept。若声称 previous_open_findings 已解决，resolved_findings 必须给出 check_id，且该检查应是同一特征/参数的 match。

严格返回 JSON：

{"decision":"accept|revise|ask_user","similarity_score":0.0,"checks":[{"id":"front.outline","view":"front","area":"outline","category":"geometry","status":"match|mismatch|partial|unknown","item":"核对项","evidence":"具体图像与参数证据","unknown_reason":"status=unknown 时必填","feature_ids":["left_segment","middle_segment"],"affected_parameters":[{"feature_id":"middle_segment","path":"parameters.length"}]}],"resolved_findings":[{"issue_id":"depth.middle_setback","check_id":"right.middle_setback","evidence":"说明新预览中的可见变化"}],"remaining_differences":[],"plan":null,"questions":[],"confidence":0.0}

decision=revise 时 plan 必须是完整 CADPlan；accept 时 plan 为 null。不要为了提高评分虚构精确尺寸。
