# 可扩展的本地工件知识源

DSH 建模 Agent 会在首个模型请求前检索这里登记的本地工件知识。当前内置来源是
`datasets/训练提示词/全部提示词.jsonl`；额外的 JSONL 文件可以直接放进本目录，
无需修改检索器代码。检索只加载字段化文本，不会把整个语料塞进模型上下文。

## JSONL 格式

每行一个 JSON 对象，至少提供 `prompt`、`category` 和 `model`：

```json
{"id":"custom:KX-2040","prompt":"精密导向套 KX-2040","category":"导向套","model":"KX-2040","envelope":[20,40,18],"envelope_str":"40×20×18","trust":"measured","source":"catalog/guide.json"}
```

字段说明：

- `prompt`：一种真实用户可能使用的描述或型号写法；同一型号可有多行别名。
- `category`、`model`：类别和规范型号。未知/库外负例使用 `null`，不会作为正向模型结果。
- `envelope`：按 X、Y、Z 排列的包围盒毫米数；没有可靠测量时用 `null`。
- `trust`、`source`：数据可信级别和原始出处。不要把推算值标成实测值。
- 可选 `group`、`variant`：用于归并同一工件的提示词变体。

精确型号命中可以附带项目现有 `reallib` 的实测摘要；相似描述只作为参考，不能直接覆盖目标尺寸。搜索分数是词面排序分数，不代表正确概率。

## 新检索后端

代码可通过 `ai_modeler.parts.library.retrieval.register_provider(name, provider)` 注册实现
`search(query, limit, image_paths=None)` 返回格式的向量、图像或数据库 Provider；图像检索
Provider 设置 `supports_images = True` 即会收到原始图片路径。当前 JSONL 之外，
项目自带的真实照片与 AI 渲染样本会合成为单张带型号标签的对照图，交给多模态 Agent
判断视觉相似性；这只是小型样例库，并非完整的图像向量索引。
