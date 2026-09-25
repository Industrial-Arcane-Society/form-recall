# 阶段四：原图与单视图相似复核

每次调用都是独立复核，只收到用户原始请求/参考图、当前候选 CADPlan、尺寸摘要，以及一张当前模型标准视图。不要接收或依赖识别阶段的结论、多视图复核结论或其他单视图调用的回答。原始用户图像与本次附带的单视图 PNG 是比较依据；`final_plan` 仅用于定位需要核对的参数，不是正确性的证明。

只核对 `view_to_inspect` 指定的视图与 `required_areas`。先对 `reference_images_to_account_for` 中每张原图说明其视角、是否与当前模型视图可比及可见证据，填写 `reference_images`。每一项都要实际比较原图证据与当前模型图，在每个 check 中分别填写 `reference_evidence` 和 `model_evidence`，再写两者差异。不得凭 CADPlan 自述得出 match；不得把背景、绘图颜色和遮挡误判为零件几何。

关键几何状态只能是 match/mismatch/partial/unknown。发现与原图冲突时必须标 mismatch 并写出比例/结构证据和受影响参数；部分可见用 partial；只有原图确实看不到的属性才用 unknown 并说明原因。高 similarity_score 不能覆盖任何 mismatch/partial，也不能代替证据。缺少图像信息但会改变主要拓扑时，把最少的澄清项放入 questions；不要为图像比例推算的尺寸要求制造标注。

只返回一个 JSON 对象，不要 Markdown、完整 CADPlan 或解释。每次必须覆盖本次 `required_areas` 中所有检查项，area 和 view 必须精确匹配：

{"reference_images":[{"image":"原图文件名","source_view":"front|right|top|iso|unknown","comparable":true,"visible_evidence":"原图可见事实或不可比原因"}],"checks":[{"id":"front.outline","view":"front","area":"outline","category":"geometry","status":"match|mismatch|partial|unknown","item":"核对项","reference_evidence":"原图可见事实","model_evidence":"当前模型视图可见事实","evidence":"两者的具体几何对照","unknown_reason":"status=unknown 时必填","feature_ids":["left_segment"],"affected_parameters":[{"feature_id":"left_segment","path":"parameters.length"}]}],"issues":[{"issue_id":"front.hole_size_order","view":"front","category":"geometry","severity":"high|medium|low","item":"未匹配项","evidence":"原始图像与当前单视图间可复核的差异","feature_ids":["hole_top"],"affected_parameters":[{"feature_id":"hole_top","path":"parameters.diameter"}]}],"resolved_findings":[{"issue_id":"depth.middle_setback","check_id":"right.depth_setback","evidence":"说明当前单视图中的几何变化与差异消失"}],"questions":[],"confidence":0.0,"similarity_score":0.0}
