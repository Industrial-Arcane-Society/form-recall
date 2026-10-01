# 原图独立盲读

这是独立读图请求，只依据用户原始文字和本消息中的参考图；不使用 CADPlan、建模器的读图结论或模型预览。

按图片逐张记录视角与画面覆盖。每个可见实体面单独一条区域；区分普通实体面、后退实体面、背景贯通开口和无法判断。统计该面可见的孔、槽、凸台等，同一平面的相似孔合并成一组并记录数量/排列。不同视图可能看到同一孔，绝不跨图相加。标注图像中确实无法判定的歧义；没有歧义就留空。

重复孔组还必须逐个按 `arrangement` 所写顺序记录 `relative_size_order`：`large`、`medium`、`small`、`same` 或 `unknown`。例如清楚可见的上、中、下孔为大—小—大，就返回 `["large","small","large"]`，`size_order_axis="vertical_top_to_bottom"`，并用 `size_order_confidence="high"`。`same` 表示这一整组孔等大，必须对每个实例重复填写；不要用它表示“与前一个大小相近”。图中颜色可以帮助区分已计数孔的尺寸，但不同颜色不代表额外孔；不要把颜色当作直径单位。只有边缘清楚可比较时才给出 high/medium；被遮挡、透视或分辨率影响无法可靠排序时逐项填 `unknown` 并降低置信度。这个相对顺序是像素可见事实，不要求估计毫米直径。

只写像素可核对的证据，不估计毫米尺寸，不写推理过程或重复描述。每张图最多 8 个区域；每个 evidence、arrangement 和 uncertainty 不超过 120 个字符。完整正视图只有在能看到全部相关孔位时才标 `hole_layout_coverage=complete`。

观察完成后必须调用 `submit_reference_inventory` 提交结果。不要在文本通道手写 JSON 或附加解释；工具 Schema 限制数量与字段长度。
