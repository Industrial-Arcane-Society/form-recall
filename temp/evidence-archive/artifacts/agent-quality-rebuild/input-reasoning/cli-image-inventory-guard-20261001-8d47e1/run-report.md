# `inputguard-20261001-8d47e1` 真实 CLI 复验

- 日期：2026-10-01（收尾核查：2026-10-02）
- CLI session：`inputguard-20261001-8d47e1`；DSH session：`inputguard-20261001-8d47e1-dsh-d15e202e9521`；turn：`4b35e98620994779b3f47d949428719d`；exec handle：`60556`（已退出，exit code 1）。
- 原图：`artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`，SHA-256 `53ED87FADF8CCA869DA921627E6A13C845424C6906B3DE517D5CCA972DB94553`。
- 用户输入：`请根据参考图生成可编辑模型；图片没有尺寸标注，按图内比例估算并标明尺寸来源。`
- 本轮提示词代码改动：`prompts/dsh_agent/system.md`；相邻回归：`tests/test_dsh_agent_cli.py`。`python -m unittest tests.test_dsh_agent_cli -v` 为 20/20 通过，`git diff --check` 通过。该本地回归只证明提示词规则文本，不代表模型验收。

## 观察结果

首次全图 `cad_inspect_references` 发生于 CLI elapsed +12.5s，provider response 于 +88.9s 返回，约 76.2s。结果 `observation_valid=true`、状态为 `model_observation_unverified`，使用整图 ROI（`roi_norm=null`），带有上述原图 SHA。摘要识别左右前壁、内后壁、各组三孔及中间矩形凹槽。整轮只有这一次 inventory；没有重复同 key，也没有 ROI 请求。提示词达成了首轮全图、缓存复用/不重问的可观察行为；但对于相对尺寸等潜在未知事实未发起定向 ROI 复核，不能判定图像事实已充分识别。

首次几何动作 `cad_create_model` 在 +179.7s（首轮 inventory 返回后约 90.8s），建立 revision 1 的 `base_block`，尺寸 120×60×80 mm。全程没有有效 preview，没有 `cad_review_reference_similarity` 或 `cad_finish_model`，也没有独立多视角复核。后续几何操作反复无进展，最终快照记录 4 次连续 no-op；revision 和 preview 未推进。总 CLI elapsed 最后记录为 +348.7s。运行在完成候选之前退出，`active-turn.json` 的 `completed_at_epoch_ms` 为空；没有保存可验收 candidate 或结果报告。不能将此轮描述为模型成功或真实图像验收通过。

## 进程和证据归档

exec handle `60556` 已结束。2026-10-02 核查到系统范围内 1 个 `python.exe` 和 1 个 `node.exe`，但按本 CLI/DSH session ID、`dsh_mcp_server`、`dsh_stream_bridge`、`deepseek_harness` 匹配的进程数为 0。核心运行证据保存在同目录 `evidence/`：active turn、agent observability、CAD state、reference images、runtime policy、transport image map、user turns。
