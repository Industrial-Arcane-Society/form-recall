# 单视图几何复核

本请求包含：一张模型当前视图 PNG、与它同方向的一张原始参考图，以及精简后的 CADPlan 和本地网格检查摘要。只有本请求附带的原始图片可作为视觉证据；未附带图片的名字或文字描述不能代替看图。只比较同方向图像。先核对原图实际视角；不能比较时返回 `comparable=false`，不得跨视角推断差异。

核对 `required_areas` 中的每项，并核对每个在本视图可见的 `independent_visible_feature_groups`。这些组只是另一条读图请求产生的线索，可能有错；回到像素重新判断数量、所在实体面和前后关系。重点看外轮廓比例、连续实体外壳、凹槽/退台、孔的数量与大小顺序、孔所在面及可见贯穿结果。颜色、背景、网格和绘图风格不属于几何。

若独立特征组包含 `relative_size_order`，逐个回看对应孔口边缘并核对每个位置的大小；该字段不能替代当前像素观察。大小顺序不一致时必须返回 `mismatch`，并把孔特征 ID 和 `parameters.diameter` 放入受影响项；若本视图看不清则返回 `unknown`，不能只核对孔数后给整个组 `match`。

`front` 的 `outline` 只比较图像平面内的 X/Z 外轮廓和宽高比例，`features` 只比较孔的二维数量、位置和大小；不要把 Y 向退台混入这两项。`iso` 的 `depth_relationships` 专门比较前后错层和凹槽深度。

每个 `match` 检查都必须分别写出原图和模型中的可见证据。没有绝对尺寸时比较相对比例；视角无法看出的深度或贯穿状态标 `unknown`。特别是 front 正交投影不含 Y 向深度信息；不得从正视图判断中间面是否退台、是否齐平或孔是否沿厚度贯穿。这些问题只由 iso/right 等有深度信息的同方向比较判断。`final_plan` 只用于定位偏差和受影响特征，不是正确性的证据。仅对新版图像实际显示已消失的差异填写 `resolved_findings`。

输出精简结果：每个证据字段只写一句；`issues` 只列可定位的 mismatch/partial，并填写受影响的 `feature_ids` 与 `affected_parameters`。不要复述 `final_plan`、重复总结或输出思考过程。

检查项的 `area` 必须从 `required_areas` 选择；独立特征组检查使用 `area=feature_group`，并把 `feature_group_id` 逐字填写为本视图 `independent_visible_feature_groups` 中提供的 id。完成观察后必须调用 `submit_multiview_review` 提交结果。不要在文本通道手写 JSON 或附加解释；工具 Schema 会限定字段、条数和证据长度。
