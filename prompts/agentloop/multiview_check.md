# 单视图几何复核

本请求附带当前模型单视图和原始参考图。`reference_images_to_account_for` 列出本视图需逐张核对的原图；它只用于识别图片和视角，不是视觉证据。所有比较都必须看随请求附带的原图与模型图，只比较同方向图像；视角不符或看不清时返回 `comparable=false`，不得跨视角推断。

核对 `required_areas` 中的每项及本视图可见的每个 `independent_visible_feature_groups`。这些组只是原图独立盲读的线索，不是事实；回到像素核实数量、所在面和前后关系，不要按颜色合并区域。重点看外轮廓比例、连续外壳、凹槽/退台、孔的数量与相对大小、所在面和可见贯穿结果。颜色、背景、网格和绘图风格不属于几何。

若独立特征组包含 `relative_size_order`，逐个回看对应孔口边缘并核对每个位置的大小；该字段不能替代当前像素观察。大小顺序不一致时必须返回 `mismatch`，并把孔特征 ID 和 `parameters.diameter` 放入受影响项；若本视图看不清则返回 `unknown`，不能只核对孔数后给整个组 `match`。

`front` 的 `outline` 只比较图像平面内的 X/Z 外轮廓和宽高比例，`features` 只比较孔的二维数量、位置和大小；不要把 Y 向退台混入这两项。`iso` 的 `depth_relationships` 专门比较前后错层和凹槽深度。

每个 `match` 检查都必须分别写出原图和模型中的可见证据。没有绝对尺寸时比较相对比例；视角无法看出的深度或贯穿状态标 `unknown`。特别是 front 正交投影不含 Y 向深度信息；不得从正视图判断中间面是否退台、是否齐平或孔是否沿厚度贯穿。这些问题只由 iso/right 等有深度信息的同方向比较判断。`final_plan` 只用于定位偏差和受影响特征，不是正确性的证据。仅对新版图像实际显示已消失的差异填写 `resolved_findings`。

只提交精简检查：每个证据字段写一句短句，分别指出原图或模型图上实际可见的线索；`issues` 只列可定位的 mismatch/partial，填写受影响的 `feature_ids`、`affected_parameters`，并把对应检查项的 `area` 原样写入 issue。后续结案只能由同一视图、同一可判定 area 的 `match` 检查支持；视角或检查区域不能判断该差异时，保留为 `unknown`，不要结案。不要重复同一结论、复述 `final_plan` 或输出思考过程。

检查项的 `area` 必须从 `required_areas` 选择；独立特征组检查使用 `area=feature_group`，并把 `feature_group_id` 逐字填写为本视图 `independent_visible_feature_groups` 中提供的 id。完成观察后必须调用 `submit_multiview_review` 提交结果。不要在文本通道手写 JSON 或附加解释；工具 Schema 会限定字段、条数和证据长度。
