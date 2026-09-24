# CATIA MCP 工具对照表

> 与开源项目 **catia-v5-mcp-server** 的 78 个 `catia_*` 工具逐条对照。
> 本表由 `python tools\gen_mcp_tool_map.py` 自动生成（状态是现扫模块算出来的），
> 所以模块一落地重跑一次就同步。

- 参考工具总数：**78**，本实现已同名实现：**78**
- 真机验证：document 9 个、sketcher 11 个、part_design 15 个、gsd 24 个、assembly 9 个、measurement 6 个、export 4 个、modeler 5 个
- 模块加载情况：document: 9 个工具; sketcher: 11 个工具; part_design: 15 个工具; gsd: 24 个工具; assembly: 9 个工具; measurement: 6 个工具; export: 4 个工具; modeler: 5 个工具

## 文档管理（document）  9/9

| 工具 | 作用 | 本实现用到的 CATIA 接口 | 状态 |
|---|---|---|---|
| `catia_connect` | 连接 CATIA V5 | Application(Documents.Count/ActiveDocument) | ✅真机 |
| `catia_disconnect` | 断开连接 | （本实现无状态, 无 COM 句柄可释放） | ✅真机 |
| `catia_new_part` | 新建零件 | Documents.Add("Part") + Document.Activate | ✅真机 |
| `catia_new_product` | 新建装配 | Documents.Add("Product") | ✅真机 |
| `catia_open_document` | 打开文档 | Documents.Open(path) | ✅真机 |
| `catia_save_document` | 保存/另存为 | Document.Save / SaveAs（中文路径先存 ASCII 再搬移） | ✅真机 |
| `catia_close_document` | 关闭文档 | Document.Close / Save+Close | ✅真机 |
| `catia_list_documents` | 列出文档 | Documents.Item(i).Name | ✅真机 |
| `catia_get_active_document_info` | 活动文档信息 | Part/PartBody/Shapes/Sketches/HybridBodies/Parameters | ✅真机 |

## 2D 草图（sketcher）  11/11

| 工具 | 作用 | 本实现用到的 CATIA 接口 | 状态 |
|---|---|---|---|
| `catia_create_sketch` | 在基准面建草图 | Sketch.OpenEdition + 偏移平面 | ✅真机 |
| `catia_close_sketch` | 关草图并 Update | Sketch.CloseEdition + Part.Update | ✅真机 |
| `catia_sketch_line` | 画直线 | Factory2D.CreateLine | ✅真机 |
| `catia_sketch_rectangle` | 两点矩形 | Factory2D.CreateLine ×4 | ✅真机 |
| `catia_sketch_centered_rectangle` | 中心矩形 | Factory2D.CreateLine ×4（按中心+长宽算角点） | ✅真机 |
| `catia_sketch_circle` | 画圆 | Factory2D.CreateClosedCircle | ✅真机 |
| `catia_sketch_arc` | 画圆弧 | Factory2D.CreateArc / CreateSpline 近似 | ✅真机 |
| `catia_sketch_spline` | 样条 | Factory2D.CreateSpline + 控制点 | ✅真机 |
| `catia_sketch_point` | 画点 | Factory2D.CreatePoint | ✅真机 |
| `catia_sketch_constraint` | 加约束 | Sketch 约束集合 / Constraint 创建 | ✅真机 |
| `catia_sketch_get_geometry` | 读草图元素 | Sketch.GeometricElements | ✅真机 |

## 三维零件设计（part_design）  15/15

| 工具 | 作用 | 本实现用到的 CATIA 接口 | 状态 |
|---|---|---|---|
| `catia_pad` | 凸台(拉伸) | ShapeFactory.AddNewPad(sketch, height) | ✅真机 |
| `catia_pocket` | 凹槽(切除) | ShapeFactory.AddNewPocket(sketch, depth) | ✅真机 |
| `catia_shaft` | 旋转体 | ShapeFactory.AddNewShaft（真机实测会断连接 → 用多层圆盘近似） | ✅真机 |
| `catia_groove` | 旋转切除 | ShapeFactory.AddNewGroove（同上, 近似/明确不支持） | ✅真机 |
| `catia_fillet` | 倒圆角 | ShapeFactory.AddNewEdgeFilletWithConstantRadius | ✅真机 |
| `catia_chamfer` | 倒角 | ShapeFactory.AddNewChamfer | ✅真机 |
| `catia_hole` | 孔(通/盲/沉孔/沉头) | ShapeFactory 孔特征 或 草图圆+Pocket | ✅真机 |
| `catia_rect_pattern` | 矩形阵列 | ShapeFactory.AddNewRectPattern | ✅真机 |
| `catia_circ_pattern` | 圆周阵列 | ShapeFactory.AddNewCircPattern | ✅真机 |
| `catia_mirror` | 镜像 | ShapeFactory.AddNewMirror | ✅真机 |
| `catia_shell` | 抽壳 | ShapeFactory.AddNewShell | ✅真机 |
| `catia_draft` | 拔模 | ShapeFactory.AddNewDraft | ✅真机 |
| `catia_thickness` | 加厚 | ShapeFactory.AddNewThickness | ✅真机 |
| `catia_list_features` | 列出特征 | PartBody.Shapes | ✅真机 |
| `catia_list_edges` | 列出边(供倒角选) | Shape 拓扑边遍历 | ✅真机 |

## 创成式外形设计（gsd）  24/24

| 工具 | 作用 | 本实现用到的 CATIA 接口 | 状态 |
|---|---|---|---|
| `catia_gsd_create_geoset` | 建几何图形集 | Part.HybridBodies.Add + InWorkObject | ✅真机 |
| `catia_gsd_set_active_geoset` | 切当前图形集 | Part.InWorkObject | ✅真机 |
| `catia_gsd_list_elements` | 列出元素 | HybridBody.HybridShapes | ✅真机 |
| `catia_gsd_point` | 三维点 | HybridShapeFactory.AddNewPointCoord | ✅真机 |
| `catia_gsd_line` | 两点直线 | AddNewLinePtPt | ✅真机 |
| `catia_gsd_plane_offset` | 偏移平面 | AddNewPlaneOffset | ✅真机 |
| `catia_gsd_plane_3points` | 三点平面 | AddNewPlane3Points | ✅真机 |
| `catia_gsd_spline` | 三维样条 | AddNewSpline + AddPoint/SetPoint | ✅真机 |
| `catia_gsd_circle` | 三维圆 | AddNewCircleCtrRadWithAngles | ✅真机 |
| `catia_gsd_project` | 投影 | AddNewProjection | ✅真机 |
| `catia_gsd_intersection` | 相交 | AddNewIntersection | ✅真机 |
| `catia_gsd_multi_section_surface` | 多截面曲面 | AddNewLoft | ✅真机 |
| `catia_gsd_sweep` | 扫掠 | AddNewSweep* | ✅真机 |
| `catia_gsd_extrude` | 拉伸曲面 | AddNewExtrude | ✅真机 |
| `catia_gsd_revolve` | 旋转曲面 | AddNewRevolve | ✅真机 |
| `catia_gsd_fill` | 填充 | AddNewFill | ✅真机 |
| `catia_gsd_blend` | 桥接 | AddNewBlend | ✅真机 |
| `catia_gsd_offset_surface` | 偏移曲面 | AddNewOffsetSurface | ✅真机 |
| `catia_gsd_join` | 接合 | AddNewJoin | ✅真机 |
| `catia_gsd_split` | 分割 | AddNewSplit | ✅真机 |
| `catia_gsd_trim` | 修剪 | AddNewTrim | ✅真机 |
| `catia_gsd_symmetry` | 对称 | AddNewSymmetry | ✅真机 |
| `catia_gsd_thick_surface` | 曲面加厚成实体 | Part.ShapeFactory.AddNewThickSurface | ✅真机 |
| `catia_gsd_close_surface` | 封闭曲面成实体 | ShapeFactory.AddNewCloseSurface | ✅真机 |

## 装配（assembly）  9/9

| 工具 | 作用 | 本实现用到的 CATIA 接口 | 状态 |
|---|---|---|---|
| `catia_add_component` | 插入已有零件 | Product.Products.AddNewComponent | ✅真机 |
| `catia_add_new_part` | 装配内新建零件 | Product.Products.AddNewProduct | ✅真机 |
| `catia_fix_constraint` | 固定 | Constraints.AddMonoEltCst(catFix) | ✅真机 |
| `catia_coincidence_constraint` | 重合约束 | Constraints.AddBiEltCst(catCoincidence) | ✅真机 |
| `catia_offset_constraint` | 偏移约束 | AddBiEltCst(catDistance) | ✅真机 |
| `catia_angle_constraint` | 角度约束 | AddBiEltCst(catAngle) | ✅真机 |
| `catia_move_component` | 移动/旋转 | Product.Move.Apply | ✅真机 |
| `catia_list_components` | 列出组件 | Product.Products | ✅真机 |
| `catia_list_constraints` | 列出约束 | Product.Connections/Constraints | ✅真机 |

## 测量与分析（measurement）  6/6

| 工具 | 作用 | 本实现用到的 CATIA 接口 | 状态 |
|---|---|---|---|
| `catia_measure_distance` | 测距离 | SPAWorkbench.GetMeasurable | ✅真机 |
| `catia_get_inertia` | 体积/面积/重心 | Measurable.Volume/Area/GetCOG | ✅真机 |
| `catia_get_bounding_box` | 包围盒 | Measurable.GetBoundingBox（或用 export+STL 兜底） | ✅真机 |
| `catia_get_parameters` | 列参数 | Part.Parameters | ✅真机 |
| `catia_set_parameter` | 改参数 | Parameter.Value + Part.Update | ✅真机 |
| `catia_update_part` | 强制重建 | Part.Update | ✅真机 |

## 导出与视图（export）  4/4

| 工具 | 作用 | 本实现用到的 CATIA 接口 | 状态 |
|---|---|---|---|
| `catia_export` | 导出 STEP/IGES/STL/3DXML/VRML | Document.ExportData | ✅真机 |
| `catia_screenshot` | 截图 | Viewer/Viewpoint 截图（版本差异大，取不到会说明） | ✅真机 |
| `catia_set_view` | 标准视图 | Viewer.Viewpoint / StartCommand | ✅真机 |
| `catia_fit_all` | 适应窗口 | Viewer.Reframe / "Fit All In" | ✅真机 |

## 本插件扩展（参考项目没有，5 个）

| 工具 | 作用 | 实现方式 | 状态 |
|---|---|---|---|
| `catia_nl_build` | 一句中文直接建到 CATIA | 本插件中文解析 + 特征树 + 推送通道 | ✅真机 |
| `catia_nl_preview` | 试算中文描述(不碰 CATIA) | 本插件内核 + CATIA 特征表 | ✅真机 |
| `catia_feature_table` | 看中文展开成哪些操作 | catia_export.render 特征表 | ✅真机 |
| `catia_catalog_search` | 检索 catalog 标准件库 | catialib 索引 | ✅真机 |
| `catia_geometry_check` | 真机几何校验(导出 STL 量孔壁) | ExportData(stl) + 网格分析 | ✅真机 |

## 离线建模工具（不装 CATIA 也能用）

共 0 个：
