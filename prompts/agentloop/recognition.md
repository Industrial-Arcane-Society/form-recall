# 阶段一：初始识别

区分图片来源：用户消息中的图片是原始参考图；历史助手消息中标注为“本地模型预览”的图片是当前/上一轮生成结果，只代表当前几何状态，不能误当成目标参考图。

仔细查看当前消息的文字和所有参考图，识别每张图的视角、可见外轮廓、面与台阶关系、孔/槽/重复特征、遮挡与不确定之处。若有正交正视图，以它作为左右宽度和孔布局的主要证据；不要从等轴测投影估计正视宽度。沿 X 方向量出各主要分段在正视图中的像素宽度，并除以总宽度，按从左到右输出 front_segment_width_ratios（总和约为 1）；看不清时填 null 并说明原因。比如两侧宽度各约为中段两倍，应记录约 [0.40,0.20,0.40]，不能写成 [0.40,0.36,0.40] 这类近等宽比例。正视图中也记录孔位比例及每排/每列孔径的相对等级；如果首末排孔明显大于中间排，明确记录“大—小—大”。等轴测图中区分各段朝向观察者的前表面、后表面及前后错层；若视角允许沿深度方向估算中段前表面相对侧段的后退量，除以整体高度，输出 middle_front_setback_height_ratio，否则填 null 并解释。该值是几何 Y 后退量/Z 高度的比例，不是屏幕像素位移，也不是中段厚度比例。先把显式标注与图像估值分开，不要在这一阶段编造精确制造尺寸或生成 CADPlan。

如果消息中同时包含 `three_region_scanline_candidate` 或 `foreground_bbox_candidate` 像素扫描结果，先判断该记录所属图片的实际视角。只有该图确实是正交正视图、扫描线横穿三段平面填色区域时，才把其 `width_ratios` 直接作为 `front_segment_width_ratios`，并在 notes 中注明来自本地像素扫描；不要重新凭视觉记忆估计同一比例。该图确为正视图且 bbox confidence 为 high 时，直接采用 `width_height_ratio` 作为 `front_width_height`。等轴测图扫描得到的比例不能当作正视宽度或宽高比。

如果用户要求的形状依赖外部标准或材料数据，可以调用 `web_search`；否则不要搜索。网页结果不能覆盖用户图像证据。

最终只返回 JSON：
{"views":[{"image":"文件名或序号","view":"front/right/top/iso/unknown","evidence":"可见证据"}],"requirements":[{"item":"几何要求","evidence":"文字或图像证据","certainty":"explicit/visible/uncertain"}],"proportion_observations":{"front_width_height":null,"depth_height":null,"front_segment_width_ratios":null,"middle_front_setback_height_ratio":null,"notes":"只给有根据的比例或 null，并说明后退比例的图像依据"},"feature_layout":[],"pending_questions":[]}

比例须基于零件外轮廓，不把背景、网格、标注算入轮廓。不同视图只能按其投影方向解释；不要把 Y 深度当成 Z 高度。区分“厚度不同”和“前表面后退”：前者本身不说明哪一面齐平。若输入只有文字，也照实说明没有图像证据。
