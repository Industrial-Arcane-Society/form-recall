# 图片建模的简短结构清单

读图只为开始建模服务。把主体外形与图中可见的重复特征按区域分组，写成 `reference_reading`，并放进同一次 `cad_create_model` 调用。不要先输出长篇推理或等待用户给尺寸。

```json
{
  "feature_groups": [
    {
      "id": "main-body",
      "kind": "body",
      "quantity": 1,
      "region": "整体外轮廓",
      "arrangement": "单一主体",
      "evidence": "图中可见的连续外轮廓"
    },
    {
      "id": "hole-row-a",
      "kind": "hole",
      "quantity": 3,
      "region": "同一可见表面",
      "arrangement": "沿一方向等距排列",
      "evidence": "表面上有三个重复圆形开口"
    }
  ],
  "uncertainties": ["无法从当前视角确认的隐藏连接关系"]
}
```

- 每组只写 `id`、`kind`、`quantity`、`region`、`arrangement`、`evidence`；`uncertainties` 可选。`quantity` 是可见且不同的特征数，不是渲染出来的彩色区域数。
- 按不同可见表面或区域分别列组。不要仅凭颜色、相似外观或大致对齐，把不同区域的开口合并成一条隐藏的孔轴；无法确认的关系放进 `uncertainties`，先建立最符合可见轮廓的候选。
- 不必计算所有可见孔口总数，不必判断孔相对某种特定槽壁的方向，也不必推断图外的隐藏拓扑。孔是否贯穿以可见表面证据和后续模型视图复核为准。
- 没有标注尺寸时，直接从图内宽高厚和特征间距估比例，选择方便编辑的名义尺度；所有推算尺寸标记为 `image_estimate` 并说明比例依据。
- 首次回复尽量只包含 `cad_create_model` 工具调用和一句短进展。该调用创建第一个主体；之后每次只添加或修改一个特征，主体完成后再请求多模态单视图复核。
