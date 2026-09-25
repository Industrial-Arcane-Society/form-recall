# 参考图的结构清单

在 `cad_create_model` 前，读图一次并把结果放进 `reference_reading`。只写后续建模和复核会用到的内容，不先写长篇方案。

```json
{
  "visible_openings": 4,
  "hole_count": 2,
  "walls": 0,
  "hole_vs_walls": "none",
  "feature_groups": [
    {
      "id": "holes-region-a",
      "kind": "hole",
      "quantity": 2,
      "visible_openings": 4,
      "region": "面 A，出口位于与其同轴的面 B",
      "arrangement": "两个分开的孔轴",
      "evidence": "两组入口和出口分别同轴，因此四个孔口归并为两个物理孔"
    },
    {
      "id": "main-body",
      "kind": "body",
      "quantity": 1,
      "visible_openings": 0,
      "region": "工件整体",
      "arrangement": "单一主体",
      "evidence": "外轮廓是一个连续实体"
    }
  ],
  "evidence": "只合并有同轴入口和出口证据的孔口",
  "color_usage": "颜色用于区分面，不作为材质或孔口归属的单独证据"
}
```

规则：

- `visible_openings` 是图中可见的孔口数；`hole_count` 是有证据支持的不同物理孔轴数。**不得默认用孔口数除以二。**盲孔通常只有一个可见孔口；相对两面上的孔口只有在位置和轴向能对应时才可合并。
- `feature_groups` 按不同实体区域和重复布局列出可见结构。每项写稳定 `id`、特征 `kind`、物理数量 `quantity`、可见孔口数 `visible_openings`、区域、排列和图像依据。孔组的 `quantity` 之和必须等于 `hole_count`。主体、凹槽、台阶、肋、凸台和不同面上的孔组都要分别记录；不要把不同区域只因颜色相同就并成一组。
- 多张图默认是同一工件的不同视角。不同视图中确认是同一物理特征时去重；仅凭颜色、相似大小或看起来对齐不能判定为同一孔。无法确认时保留为独立候选，并在 `evidence` 里标出不确定点，后续用模型单视图复核。
- 只记录图上有证据的结构。隐藏区域的数量、连接方式或贯穿关系不能从单张图确认时，写明推断和不确定性；不要为了填字段编造精确拓扑。
- `walls` 只在存在槽或通道侧壁时计数。`hole_vs_walls` 取 `through_thickness`（孔轴平行于两壁分隔方向）、`along_face`（孔轴垂直于两壁分隔方向）或 `none`（没有可比较的槽壁，或没有孔）。有槽壁且有孔时，必须用孔口椭圆短轴方向和开口所在面判断孔轴；若无法判断，先保留候选并把问题留给复核。
- 所有图像估值尺寸在 CADPlan 数值来源中标为 `image_estimate`，并写明依据。无绝对尺寸不妨碍先建候选。

程序会检查字段格式、孔组数量和特征方向与模型的矛盾。读图清单是可复核的假设，不代表已证明模型正确；结束前仍须将每组结构与原图及当前模型单视图核对。
