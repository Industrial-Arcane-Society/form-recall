# 冷图 Agent CLI 验收报告

## 结论

本次验收未完成，模型候选未验收。新的 inventory 写入与 SHA 门禁在真实 CLI 中通过，但 Agent 未在停止前生成成功预览或独立视觉复核。停止前出现几何构建失败、重复特征变更与删除；没有导出 AIMODEL、STL、预览图或最终结果 JSON。

## 输入与运行

- 会话：`coldimg20261001inventoryfix`，exec session `66450`
- 原图：`artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`
- 原图 SHA-256：`53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`（72,309 bytes）
- 唯一用户任务：`请根据参考图生成可编辑模型；图片没有尺寸标注，按图内比例估算并标明尺寸来源。`
- 模型：`moonshotai/Kimi-K2.7-Code`；CLI显示推理强度 `low`
- provider wire：36 个请求，35 个完整响应，末个在途请求被 Ctrl+C 中断；36 次中仅 1 次附图，是独立原图 inventory；没有独立原图/候选模型多模态 review pair。

## 验收证据

- 初次 `cad_inspect_references` 返回后，`cad-state.json` 中 observation_valid=true、reference_id 与 source SHA 均匹配原图，roi_norm=null，存在3组非空可见事实：左外侧面3孔、右外侧面3孔、退入内背面3孔。验证状态仍为 `model_observation_unverified`。这证明 inventory 解析、成功写入门禁索引和同 SHA 建模准入在本次运行生效。
- Agent 曾创建 revision 1；经历多次无变化更新、建模尝试及特征删除后，曾自行增加特征至 revision 14。期间出现重复 `center_slot` 名称变体及孔特征反复增加/删除，模型结构多次变化。
- 在 revision 10 时 `cad_preview_model` 报告 `geometry_build_failed`。停机时最终 state revision 14，特征 ID 为 `base_block`, `center_slot`, `hole_left_outer_top_v2`；没有预览、没有独立视觉复核、没有当前有效前缀/最佳候选记录。
- 约 12 分 45 秒时监督端发送 Ctrl+C。exec session 返回 exit_code=1；之后按 session id 核查 Python/Pythonw/Node/DSH 进程均为0。`cad-state.json` 与 `agent-observability.jsonl` 已落盘并复制。
- 输出目录由监督端在进程停止后创建。原 CLI stdout/stderr 没有单独落盘；实时进度经 exec 会话返回。`agent-observability.jsonl` 是保留下来的脱敏 wire / DSH / tool activity 审计，不包含API key匹配项。
- `tests.test_dsh_review_workflow` 49/49 通过仅表示本地定向单测通过，不代表真实建模验收通过。

## 文件

- `cad-state.json`：停止时完整状态与 tool actions
- `agent-observability.jsonl`：wire 请求、usage、tool 与流进度审计
- `reference-images.json`、`user-turns.json`、`active-turn.json`：输入清单与当前turn元数据
- `transport-image-map.json`、`runtime-capacity-policy.json`：传输图像映射和资源策略元数据
- `run-manifest.json`：会话、原图SHA、停止状态与交付清单
- `observed-cli-progress.md`：监督端可见的关键进度事件；不是完整stdout/stderr副本
