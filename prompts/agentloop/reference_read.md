# 图片建模的独立读图阶段

本阶段只看原始参考图并调用 `cad_inspect_references`，不要同时创建 CAD 特征，不要询问用户尺寸。把识图和几何生成分开，避免在一次调用里一边猜拓扑、一边忙着建实体。

`reference_reading` 要同时包含 `feature_groups` 与 `visible_regions`。`feature_groups` 记录目标整体和重复特征组；`visible_regions` 必须逐张图、逐个可见平面记录：文件名、归一化图像框 `[left,top,right,bottom]`、实体面/后退实体面/背景开口/未知状态、区域像素证据，以及该区域实际可见的孔槽数量和排列。不同深度或颜色的表面只要边界不同就分开列，不能把有孔的后退平面描述成背景空隙。看不清时标 `unknown`，并说明像素证据，不用猜。

同一张图中 `feature_groups` 的孔数总和必须与 `visible_regions` 逐面孔数总和一致；程序会拒绝互相矛盾的读图清单，让你回看原图后再进入建模。多张图是同一工件的不同视角，不得把不同视图重复展示的同一孔跨图相加。

不同深度的面和开口可以因透视出现部分投影重叠，但不能用几乎相同的 `image_bbox_norm` 同时表示“后退实体面”和“背景开口”。若确实能同时看到开口前缘和后退底面，分别框出各自可见像素区域；若边界不足以区分，先把该区域标为 `unknown` 并记录不确定性，不要让两种互斥解释同时进入建模。

拓扑判断先看整体外轮廓和跨区域连续的面，再看颜色分区。不要预设主体必然是完整块体、三块板、U 形件或通槽；分别记录像素能证明的面、边界、连接、遮挡和开口。仅当某处能看到背景连续穿过，才判断为贯通空隙或分离件。分别给出前视宽高比例，以及图中可见侧深相对于高度的估算；多视图可用正交视图校准比例，单一等轴图则说明透视估值的不确定性。把连续侧壁、顶面和各后退面都纳入清单，不能只统计正面孔组。

对有把握的可见比例，填入 dimensionless_constraints，只记录图像比例，不换算成制造尺寸：bbox_ratio 用一组对象、两个轴和一个宽高比；ordered_partition 用一个轴、按图中顺序列出对象和各自占总跨度的比例（总和约为 1）；normalized_position 用对象在图像或面内的 0–1 归一化坐标；relative_size 用同组特征的相对尺寸（最大值归一为 1）；setback_ratio 与 spacing_ratio 记录相关尺寸之间的比例。每项都必须引用参考图文件名、视角、像素证据和置信度；不确定的比例省略，不要编造。孔组还要按 arrangement 顺序逐项填写 relative_size_order，方向写入 size_order_axis；看不清的孔径写 unknown。

```json
{
  "feature_groups": [
    {
      "id": "main-body",
      "kind": "body",
      "quantity": 1,
      "region": "整体外轮廓",
      "arrangement": "单一主体",
      "evidence": "图中可见的连续外轮廓",
      "relative_size_order": [],
      "size_order_axis": "not_applicable",
      "size_order_confidence": "unknown"
    },
    {
      "id": "hole-row-a",
      "kind": "hole",
      "quantity": 3,
      "region": "同一可见表面",
      "arrangement": "沿一方向等距排列",
      "evidence": "表面上有三个重复圆形开口",
      "relative_size_order": ["large", "small", "large"],
      "size_order_axis": "vertical_top_to_bottom",
      "size_order_confidence": "medium"
    }
  ],
  "visible_regions": [
    {
      "image": "reference.png",
      "id": "visible-face-1",
      "region": "图像左侧的前向实体平面",
      "surface_state": "solid_face",
      "image_bbox_norm": [0.1, 0.1, 0.4, 0.9],
      "evidence": "闭合边界围住一块连续着色的平面",
      "features": [
        {
          "kind": "hole",
          "quantity": 3,
          "arrangement": "沿高度方向排列",
          "evidence": "区域框内可逐个看到三个独立圆形孔口"
        }
      ]
    }
  ],
  "dimensionless_constraints": [
    {
      "id": "front-width-height",
      "relation": "bbox_ratio",
      "image": "reference.png",
      "view": "front",
      "subjects": ["whole-object"],
      "axes": ["width", "height"],
      "values": [0.95],
      "confidence": "medium",
      "evidence": "可见外轮廓宽度约为高度的 0.95 倍"
    }
  ],
  "uncertainties": ["无法从当前视角确认的隐藏连接关系"]
}
```

- 每组写 `id`、`kind`、`quantity`、`region`、`arrangement`、`evidence`、`relative_size_order`、`size_order_axis` 和 `size_order_confidence`。`quantity` 是可见且不同的特征数，不是渲染出来的彩色区域数；非重复组用空的 `relative_size_order`。
- `image_bbox_norm` 的坐标相对于整张原图，取值 0–1；框应覆盖该面可见区域，彼此可以因透视或遮挡而邻接或部分重叠。
- 按不同可见表面或区域分别列组。不要仅凭颜色、相似外观或大致对齐，把不同区域的开口合并成一条隐藏的孔轴；无法确认的关系放进 `uncertainties`，先建立最符合可见轮廓的候选。
- 判别实面与空隙：只有能看到背景连续穿过的证据，才把区域识别为开口/通槽；若两段之间能看到有边界的内侧平面，它是后退的实体表面，不是空洞。阴影、颜色和遮挡本身都不能证明材料缺失；证据不足时把两种解释记入 `uncertainties`，优先保留图中可见的平面和连接。
- 读图记录必须与几何操作一致：若区域标为 `recessed_solid_face`，且该面上列有孔，表示槽底是带孔实体面。模型应使用盲凹槽形成该后退面，在其后保留材料；全厚贯穿切除会删除这个面，与读图清单自相矛盾。只有区域明确为 `background_opening` 时才可以贯穿切除，而且不得再把孔放到已被切空的区域。
- 分段记录每个可见平面上的孔，不要因为一个中间平面较暗或后退就漏掉其上的开口；孔数按可见孔口计数，不推断被遮挡的背面孔。
- 不必计算所有可见孔口总数，不必判断孔相对某种特定槽壁的方向，也不必推断图外的隐藏拓扑。孔是否贯穿以可见表面证据和后续模型视图复核为准。
- 没有标注尺寸时，直接从图内宽高厚和特征间距估比例，选择方便编辑的名义尺度；所有推算尺寸标记为 `image_estimate` 并说明比例依据。
- 读图清单通过 `cad_inspect_references` 返回后，下一步再调用 `cad_create_model` 创建主体，并按图像估算宽高厚和各局部比例。程序会在首个有效预览后强制进行一次原图复核，并在每累计六次几何修改后再次强制复核；你仍需自行选择可比视角、依据差异逐特征返修。右侧预览随有效修改更新。每次修改后旧版本复核不再有效，必须用新版本视图验证后才能完成。
