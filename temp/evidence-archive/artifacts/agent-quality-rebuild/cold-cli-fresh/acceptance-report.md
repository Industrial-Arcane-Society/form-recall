# 冷启动 CLI 验收记录

## 运行结论

本轮由正式 `python -m ai_modeler agent` CLI 启动，使用唯一会话 `cold-fresh-20261001-d78fc15f`，没有打开 GUI、续话或给模型补充结构答案。最终结果为 `candidate_not_verified`，CLI 结构化结果记录退出码 **4**（候选已保存但未达接受状态）。进程 PID 53732 于 2026-10-01 13:52:40 启动；stderr 的完成事件为 14:24:20，stdout/result 落盘约 14:24:21，运行约 31 分 41 秒。终止后 PID 已不存在。父进程退出码无法从已退出进程句柄恢复，采用 `result.json` 中的 `cli_exit_code: 4`。

运行记录：

- stdout：[stdout.txt](stdout.txt)
- stderr：[stderr.txt](stderr.txt)
- 结构化结果：[result.json](turns/f77cb0e084214297898ed5ea30bb5cda/result.json)
- CADPlan：[candidate.aimodel](turns/f77cb0e084214297898ed5ea30bb5cda/candidate.aimodel)
- STL：[candidate.stl](turns/f77cb0e084214297898ed5ea30bb5cda/candidate.stl)
- 几何报告：[geometry-report.json](turns/f77cb0e084214297898ed5ea30bb5cda/geometry-report.json)
- 本轮调用观测：[agent-observability.jsonl](turns/f77cb0e084214297898ed5ea30bb5cda/agent-observability.jsonl)
- 预览：[preview.png](turns/f77cb0e084214297898ed5ea30bb5cda/preview.png)、[ISO](turns/f77cb0e084214297898ed5ea30bb5cda/views/iso.png)、[正视](turns/f77cb0e084214297898ed5ea30bb5cda/views/front.png)

## 过程与运行时异常

- DSH 逻辑请求 60 次、provider wire 尝试 63 次；首次工具约 27.2 秒。报告用量合计 5,439,299 tokens，其中输入 5,401,650、输出 37,649，报告的 cached input 为 5,243,520。
- 63 次 provider 请求累计包含 344 个图片输入部分；图片数随对话历史累积，最后几次请求各有 12 张。原图 SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553` 多次出现在请求里，后续还重复携带了多个模型生成的预览图（transport 哈希为 unknown source mapping）。stderr 中 DSH 边界常先报“0张”，紧接着 provider wire 层显示实际带图；应以 wire capture 为准。
- 观测到 3 次 provider `TimeoutError`，均为 `python_urllib` 非流式请求在 150 秒超时；具体失败请求哈希/记录见 `agent-observability.jsonl`，CAD 状态记录 iso 相似性复核在 150 秒超时。CLI 未重启，进程自然结束。
- Agent 自己报告 `repair_cycles: 45`，工具调用 111 次：`cad_update_feature` 34、`cad_add_feature` 23、`cad_remove_feature` 11、`cad_create_model` 6、预览 12 次、参考图 inspect 2 次、相似度审查 4 次。早期有重复建模循环：`right-top-hole` 一次 `duplicate_through_axis`，其后 `cut_no_material_change` 重复至 repeat_count 5；`pocket-top-hole` 的 `cut_no_material_change` 重复至 repeat_count 2，最终将该项改名为 `pocket-top-cut`。以上来自本轮 `cad-state.json` 的 geometry failure history / action ledger。
- 4 次 `cad_review_reference_similarity` 中，前 3 次因缺 `reference_id` 或 `reference_image_index` 直接失败；之后有合法引用的 iso 审查又超时。最终没有生成相似度历史/有效配对。

## 对照原图与结果

原图呈现全高中央凹槽形成的 U 形实体。孔在三个朝前的竖直面上：左外侧凸缘正面、凹槽后壁正面、右外侧凸缘正面；三面互相平行，孔轴方向相同，均为大/小/大三孔列。原图最右侧可见的外部厚度侧面是实心无孔面。

程序最终 CADPlan 是 40 × 30 × 60 mm 主体、12 × 20 × 60 mm 凹槽、3 个沿 X 的圆柱切除与 3 个沿 Y 的圆柱切除。三根 X 向切除形成两侧厚度面上的 6 个开口；三根 Y 向切除才形成凹槽后壁正面上的 3 个开口。两组孔的面法线不同：目标是三个平行朝前面均有孔，模型只有中央后壁的 3 个孔在朝前面，其余 6 个落在 X 法线侧面。不能因开口数量凑成 9 就称拓扑相符。

逐张对照原图、最终 ISO 与 front 预览：原图左右两个朝前外凸缘各有一列孔，但模型 `front.png` 中左右两栏完全无孔，只有中央后壁三孔；模型 `iso.png` 显示近侧外部厚度面有一列三孔，而该位置在原图对应的右侧厚度面是无孔实面。模型对 U 形外形与中央三孔列有部分相似，但孔所在面的法线/位置明显错误，因此九孔布局不匹配。交付根目录的 `preview.png` 是 left 正投影，只展示一个侧面孔列，无法确认凹槽或朝前三面布孔。

程序记录的 iso 和 left 自由文本 visual review 将左侧孔列称为 match/partial，但这把参考图朝前的左凸缘与模型 X 法线的侧厚度面当作对应面；其几何报告参考覆盖仍为 `coverage_status=unverified`、`comparable_pairs=0`、`supported_evidence_ids=[]`，最终 finish 也记录 `visual_review_count=0`。因此这些自评既未建立有效配对，也漏掉了面法线错位，不能作为接受证据。

## 已核验 / 估算 / 未知

**本轮可核验：**

- 输入原图确实对应当前会话：原图哈希与 `active-turn.json` 的 reference SHA 一致。
- 本地 OCCT 报告 `geometry_state=passed`、`brep_valid=true`、闭合、单一连通实体、无自由边、无自交；包围盒 40 × 30 × 60 mm。最终保存了可编辑 AIMODEL、STL 与预览。
- CLI 状态 `candidate_not_verified`，`cli_outcome=candidate_not_accepted`，`cli_exit_code=4`；未运行 CATIA，也未创建/核验 CATPart。

**估算：**

- 40 × 30 × 60、凹槽 12 × 20 × 60 mm 和图中孔径/位置均无尺寸标注，属于程序按图推测，不能当作用户尺寸。CADPlan 将主体与槽尺寸标成 `image_estimate`；孔半径、孔距/坐标等不少被标为 `derived`，其来源说明依赖假定的 Φ8/Φ6 和图像排布，却没有单独标明基础孔径是图像估值。这是参数来源追溯不完整。

**复核来源说明：** 本报告首版曾把孔数相同误当作孔面布局相符；收到 root 关于面法线错位的反馈后，我重看原图与保存预览并修正了结论。因此这份修订报告受反馈影响，不能视为一轮全新的独立盲测。

**未知 / 未通过验收：**

- 几何报告 `hole_measurement_complete=true` 同时 `hole_feature_count=0`、`planned_hole_instance_count=0`、`active_through_hole_count=0`，因为方案用通用 cylinder cuts，而非孔实例；“all_declared_holes_verified=true”在零孔计数下不能当作九个孔逐一验证。局部 BREP 有效不等于已测通孔。
- 原图覆盖未通过有效视觉配对（0 comparable pairs）；ISO 和 left 自评缺稳定 evidence IDs，无法确认所有目标事实。
- CATIA / CATPart 完全未验证。以上不是全项目验收通过结论。

## 复现输入

`python -m ai_modeler agent --text '按参考图复刻，尺寸未标注请自行估量。' --image artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png --session-id cold-fresh-20261001-d78fc15f --output-dir artifacts/agent-quality-rebuild/cold-cli-fresh`

原图 SHA-256：`53ED87FADF8CCA869DA921627E6A13C845424C6906B3DE517D5CCA972DB94553`。
