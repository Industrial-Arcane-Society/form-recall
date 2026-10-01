# 常用建模配方库

配方库把重复的 CAD 建模经验从长提示词中拆出来，供 DSH 的知识检索和建模工具使用：

- 数据卡片在 [`modeling_recipes.json`](modeling_recipes.json)，记录适用描述、特征策略、是否可执行和能力边界。
- Agent 可以用 `cad_search_recipes` 查询操作步骤。
- 对 `status=executable` 的卡片，`builder` 指向一次性工具构建器；不要让模型把已知标准件重新拆成多轮手工特征。
- `status=guidance` 只提供已知操作方法；`status=unsupported` 明确记录当前几何内核不能可靠实现的内容。

## 添加可执行配方

1. 在 JSON 增加稳定、可检索的配方 ID、别名、输入字段、建模步骤和限制。
2. 在 `ai_modeler/parts/library/recipes.py` 增加确定性 builder，并复用已有标准表或几何内核；不得在 builder 中默默猜缺失的关键尺寸。
3. 通过 CADPlan 校验器输出完整计划和参数来源，再由 DSH 保存为当前模型。
4. CATIA 需要支持时，在 `cadplan.FEATURE_SPECS` 中声明映射状态，并在 `catia_export.py` 中实现或如实标记近似/跳过。

## 当前螺纹口径

标准内六角圆柱头螺钉配方会在本地预览和 STL 网格中生成近似 ISO 公制螺旋牙型，螺距来自标准表，不再默认用光杆代替。当前按整段螺杆生成螺纹，牙端倒角、退刀槽和长螺钉的部分牙长暂未覆盖。CATIA 导出器仍只输出光杆，结果必须区分本地网格与 CATPart；不能声称 CATPart 已包含螺纹。其它任意螺纹尚无通用 CADPlan 建模特征，内螺纹仍使用标准底孔几何并保留规格说明。

首个直接构建器为 ISO 4762 / GB/T 70.1 内六角圆柱头螺钉，尺寸来源于 [`bolt_std.json`](bolt_std.json)。螺钉长度按用户输入采用；头径、头高、内六角对边、深度和螺距按标准表读取。
