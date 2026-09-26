# 图片建模的简短结构清单

读图只为开始建模服务。将主体外形、各可见表面和重复特征逐图记录为 `reference_reading`，并放进 `cad_inspect_references` 调用。不要先输出长篇推理或等待用户给尺寸。

`reference_reading` 是后续建模和复核使用的结构化证据；不要在普通正文重复输出 JSON 或展开教程式读图说明。读图要覆盖完整可见区域，并用像素边界、归一化位置和无量纲比例表达证据。看不清的关系放入 `uncertainties`，不要编造比值；尺寸未知时仍可选方便编辑的名义尺度并保留图像比例。

```json
{
  "feature_groups": [
    {
      "id": "main-body",
      "kind": "主体外形",
      "quantity": 1,
      "region": "整体外轮廓",
      "arrangement": "单一主体",
      "evidence": "图中可见的连续外轮廓"
    },
    {
      "id": "surface-feature-set-a",
      "kind": "可见特征类别",
      "quantity": 2,
      "region": "图像中可见的表面",
      "arrangement": "按实际可见次序描述",
      "evidence": "按各特征可辨认的边界记录",
      "relative_size_order": ["large", "small"],
      "size_order_axis": "horizontal_left_to_right",
      "size_order_confidence": "medium"
    }
  ],
  "dimensionless_constraints": [
    {
      "image": "reference-1.png",
      "view": "unknown",
      "target": "待测可见范围",
      "baseline": "同图可比范围",
      "axis": "unknown",
      "ratio": 1.2,
      "confidence": "medium",
      "evidence": "示例比例值；实际值必须重新按当前图像估算"
    }
  ],
  "visible_regions": [
    {
      "image": "reference-1.png",
      "id": "surface-a",
      "region": "图像中可见表面",
      "surface_state": "solid_face",
      "image_bbox_norm": [0.1, 0.1, 0.9, 0.9],
      "evidence": "闭合边缘围成连续实面",
      "features": [
        {
          "kind": "可见特征类别",
          "quantity": 2,
          "arrangement": "按实际观察到的排列描述",
          "evidence": "示例边界证据；按当前图像重写",
          "normalized_centers": [[0.3, 0.4], [0.7, 0.6]],
          "relative_size_order": ["large", "small"],
          "size_order_axis": "horizontal_left_to_right",
          "size_order_confidence": "medium"
        }
      ]
    }
  ],
  "uncertainties": ["无法从当前视角确认的隐藏连接关系"]
}
```

- 上面的 JSON 仅演示字段关系；不得照搬示例中的特征数量、类别、视图、坐标、比例或大小顺序。每次都应从本轮一张或多张图像独立读取；没有可见比例证据时令 `dimensionless_constraints` 为空。
- 每组只写可见证据。`quantity` 是可见且不同的特征数，不是渲染出来的彩色区域数。重复特征看得清时，按原图排列顺序给出归一化中心和相对尺寸序列；不确定时用 `unknown` 并写置信度。
- `dimensionless_constraints` 记录目标相对于同图可见基准的像素比例，例如局部尺寸或间距相对于主体范围；该比例不代表毫米，也可能大于 1。只录有可见依据的关系，不同视角不要混为一条测量。
- 按不同可见表面或区域分别列组。不要仅凭颜色、相似外观或大致对齐，把不同区域的开口合并成一条隐藏的孔轴；无法确认的关系放进 `uncertainties`，先建立最符合可见轮廓的候选。
- 判别实面与空隙：只有能看到背景连续穿过的证据，才把区域识别为开口/通槽；若两段之间能看到有边界的内侧平面，它是后退的实体表面，不是空洞。阴影、颜色和遮挡本身都不能证明材料缺失；证据不足时把两种解释记入 `uncertainties`，优先保留图中可见的平面和连接。
- 分别记录每个可见平面或区域中的特征实例；不要因为某个表面较暗、被遮挡或后退就漏掉可见开口。数量按可见边界计，不推断被遮挡的背面特征。
- 按图像中的可见次序记录每组实例的位置和相对大小；不要把不同区域的特征合并成同一组，也不要推断图外隐藏拓扑。贯穿状态以可见像素证据和后续匹配视图复核为准。
- 没有标注尺寸时，直接从图内宽高、厚度线索、局部特征范围和间距估比例，选择方便编辑的名义尺度；所有推算毫米数标记为 `image_estimate` 并说明比例依据。
- 调用 `cad_inspect_references` 后，再用 `cad_create_model` 建立候选；之后每次只添加或修改一个特征。程序会逐步展示预览，并在候选建立后及累计修改后安排原图复核；按本次图像实际视角选择可比视图，不套固定相机序列。
