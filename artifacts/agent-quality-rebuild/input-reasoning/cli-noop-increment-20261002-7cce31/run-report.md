# No-op / 重复 geometry hash 提示词复验

## 单会话信息

- CLI session：`inputnoop-20261002-7cce31`；DSH session：`inputnoop-20261002-7cce31-dsh-ef3e52776021`；turn：`25c0b7065ab74c1391568e09056e05df`；exec handle `24795` 已正常退出，CLI exit code `0`，elapsed `+714.2s`（CLI monotonic progress）。
- 请求原文：`请根据参考图生成可编辑模型；图片没有尺寸标注，按图内比例估算并标明尺寸来源。`
- 原图 `artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`；SHA-256 `53ED87FADF8CCA869DA921627E6A13C845424C6906B3DE517D5CCA972DB94553`。Provider：SiliconFlow `moonshotai/Kimi-K2.7-Code`，reasoning `low`，沿用现有 CLI profile。
- 运行使用了带其它代理未提交转译修改的共享工作树；本轮观察结果不能单独归因于提示词补丁。

## 行为观察

首次 `cad_inspect_references` 的参数把 SHA 写成无 `sha256:` 前缀，并带 `reference_image_index=1`，因此返回“reference_id 不属于当前会话参考图”。模型随后只传 `reference_image_index=1` 重试一次；全图 inventory 在 CLI `+80.0s` 成功，canonical reference/source SHA 与原图一致，`roi_norm=null`。这是失败后的必要重试，不是重复成功 inventory key。

几何从 `+154.6s` 开始：revision 1 `base-block`，revision 2 `front-recess`，revision 3/4/5 分别增加左壁、右壁、内壁各三孔。共 11 个特征。没有 `cad_update_feature`，没有 `unchanged/no_op` 或重复 geometry hash，没有删除后重建；因此本次真实运行没有触发 no-op 规则，不能作为该规则成功熔断重复写入的直接证明。

revision 5 的 iso 预览生成于 `+257.2s`，preview generation 1。一次独立 review 因把 revision `5` 误传为 `preview_generation=5` 而被拒；模型改传 generation 1 后复核成功。该 pair `view_comparable=true`、coverage `partial`：主体、U 槽、左右孔数及孔径 pattern 为 match，比例为 approximate match；内壁三孔是 `unobservable`，并非差异。此 pair 的 advisory score 为 82，明确 mismatch 0、冲突 0；不能仅凭分数视为整件视觉通过。

模型随后生成 front preview（generation 2）并请求第二组原图/front pair。该 provider 调用在 `+495.2s` 开始，CLI 于 `+645.4s` 报请求失败；MCP 返回 `大模型非流式请求超过 150.0 秒`。没有 front pair 的比较结果。Agent 没有再次堆同类视图，也没有因分数追改已匹配的孔或尺寸；它校验并保存了现有几何。

## 最终状态与边界

CLI `result.json` 报 `state=accepted`、`accepted_and_verified`；`cad_finish_model` 报 `finish_state=accepted`。本地 BREP 检查通过、单一实体、bbox `100×40×80 mm`、9/9 孔实例为 `verified_open`。保存结果包括 AIMODEL、STL、geometry report，以及 iso/front 两张本地预览；CATIA 未运行。

独立视觉证据实际只有一个有效 iso pair，内壁孔仍不可观测，front pair 超时。故本报告将本轮判为**几何候选已保存，视觉复核部分完成**；程序的 accepted 状态与仅有的部分视觉证据需分开记录，不能描述为完整多视图视觉验收。

本地提示词回归：`python -m unittest tests.test_dsh_agent_cli -v`，21/21 通过。它验证提示词文本与 CLI 边界，不证明真实模型遇到 no-op 时会遵守增量决策规则。

## 文件位置

- `run-manifest.json`：请求、profile、结局和证据 hash。
- `evidence/`：运行状态、CAD state、observability、reference/transport manifest、用户回合和 DSH 原生事件轨迹（含原始工具参数及review结果/错误）。
- `turns/25c0b7065ab74c1391568e09056e05df/result.json`
- `turns/25c0b7065ab74c1391568e09056e05df/candidate.aimodel`
- `turns/25c0b7065ab74c1391568e09056e05df/candidate.stl`
- `turns/25c0b7065ab74c1391568e09056e05df/geometry-report.json`
- `turns/25c0b7065ab74c1391568e09056e05df/views/iso.png`、`views/front.png`

运行退出后按本 session、DSH session ID 和 DSH server/bridge 名称匹配的 Python/Node 进程数为 `0`。机器上另有无关的 Python 与 Node 进程，不计作本轮残留。
