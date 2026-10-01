# P0 guard 冷启动 CLI 验收报告

## 结果

**任务级验收失败，未产生可预览候选。** 本次以原始九孔凹槽图作为唯一结构来源，使用中性任务与应用配置中的模型 `moonshotai/Kimi-K2.7-Code` / low。`cad_inspect_references` 的 reference ID 与 SHA-256 匹配原图，inventory `observation_valid=true` 且有 4 条观察。其右前竖面孔数记录为 4，而原图可见 3 个孔；该错误作为视觉finding保留，未人工告知 Agent。

## P0 server guard 证据

Agent创建 `main-block` 后多次请求重建被拒；尝试删除唯一的 `operation=add` 基础实体时，server 明确拒绝，revision 保持 1、主体仍在。此 run 证实 sole-body 删除保护有效。

重复几何防护和 preview 失败有效前缀 fallback 在本次 run 中没有触发：Agent未新增第二个几何特征、未执行 preview。代码级覆盖见目标测试模块结果 53/53；这不能替代真实CLI对这两条行为的实测。

## 终态

监督端根据重复的 unchanged update、受保护的 remove/recreate 尝试、无预览和长时间没有可用候选，在同run tracker +300.9s 时 Ctrl+C 停止；CLI exit code 1。最终 revision 4 / 14 actions，只剩 `main-block` box（100×40×125 mm），没有凹槽、孔、`last_preview`、partial candidate、独立 review pair、AIMODEL/STL 或其他导出。run 中曾有 3 次几何 revision 更新，但未形成任务特征或预览，不能据此认定收敛。

## 日志与进程

停止后目标 session 对应的 Python/Node/DSH 进程匹配数为 0。Session 审计日志有 16 个 provider wire request / 15 个 response completion；DSH 侧 15 个 request start / 14 个 completion；image request 分布是 1 张图请求 1 次、0 图请求 15 次。停机时最后一个 DSH request 未完成。stdout/stderr 未被单独完整落盘；`observed-cli-progress.md` 是监督端看到的选取事件。9 个 session 源文件进行 API key / Bearer 格式扫描，命中为 0；复制了 7 个运行数据文件，排除 runtime 配置与 lock 文件。

来源 session `coldimg20261001p0guard`，exec handle 30158。完整 CAD state 和 observability jsonl 在本目录。

## 请求与无进展基线

Provider 日志记录 16 个 wire request / 15 个 response body 完成；DSH 记录 15 个 request start / 14 个 complete。图像分布为 1 张图的请求 1 次、0 图请求 15 次，停机时最后一个请求未完成。对 15 个完整 provider response body 分字段求和为 input 255,696、output 9,030、reasoning 6,139 tokens；原始 `total_tokens` 与 input/output 不一致，因此不采用。请求数、usage 和 `total_response_ms` 来自 `agent-observability.jsonl` 的 provider/DSh 事件；相对时间 `+300.9s` 来自 CLI stdout tracker，不能与其他 run 的旧 `568.4s`估值比较。

同一个 `main-block` 的CAD动作顺序见 manifest 的 `main_block_mutation_action_sequence`：创建后曾有两次cad_create_model拒绝；revision1更新有 no-op、无效 `sources` 字段和 sole-body remove拒绝；随后尺寸更新产生 revision2/3/4，最后回退历史 signature 被拒绝。没有特征级凹槽/孔进展或 preview。
