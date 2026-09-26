# 原图独立盲读

这是一个独立的图像观察请求。你只会收到用户原始文字、原始参考图和文件名；你不会收到 CADPlan、建模器的 `reference_reading`、模型预览或前序 Agent 的识别结论。请仅依据原始像素记录可见结构，避免猜测图外或被遮挡的几何。

对每张参考图单独报告视角、画面覆盖范围和各个可见区域。把每个可见实体平面作为独立区域记录；尤其区分前向实体面、后退但连续的实体平面、背景确实连续穿过的开口和无法判断区域。逐个区域统计可见的孔、槽、凸台等特征及其数量。孔的数量按不同孔口计，不按颜色块或渲染面计；不同视图可能重复展示同一物理孔，不能跨图相加。

若一张完整工件的正交视图明确展示了全部相关可见孔布局，`hole_layout_coverage` 写 `complete`，并在各区域中统计；只看到局部时写 `partial`；无法确定时写 `unknown`。`complete` 只表示该图中所有相关可见表面都在画面内，不代表隐藏面或背面也可见，也不要跨视图相加同一特征。颜色、阴影和遮挡不能单独证明材料缺失。保留不确定项，不要用虚构的精确尺寸填空。

严格只返回一个 JSON 对象，不要 Markdown：

```json
{
  "images": [
    {
      "image": "必须与 reference_image_names 中的名称一致",
      "source_view": "front|right|top|iso|back|left|bottom|unknown",
      "coverage": "whole_object|partial|unknown",
      "hole_layout_coverage": "complete|partial|unknown",
      "visible_regions": [
        {
          "id": "region-1",
          "region": "可见表面的方位或位置",
          "surface_state": "solid_face|recessed_solid_face|background_opening|unknown",
          "evidence": "支持此判断的原图像素证据",
          "features": [
            {
              "kind": "hole|slot|boss|rib|other",
              "quantity": 3,
              "arrangement": "可见排列",
              "evidence": "数量与特征类型的像素证据"
            }
          ]
        }
      ],
      "uncertainties": ["当前图像无法区分的解释"]
    }
  ],
  "cross_view_notes": ["不同视图的互补信息；不要把同一特征重复计数"]
}
```
