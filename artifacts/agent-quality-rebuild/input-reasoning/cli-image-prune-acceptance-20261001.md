# Agent CLI 输入与预览清理实测（2026-10-01）

## 结论边界

三轮由正式 `python -m ai_modeler agent` CLI 发起，使用同一应用 session `input-image-prune-20261001`，没有打开 GUI、调用 CATIA 或直接调用 SDK。三轮 CLI 退出码均为 4（`candidate_not_accepted`）；本地 BREP 检查通过，但视觉覆盖未通过/未执行，不能作为图像目标验收通过。

## 三轮记录

| 轮次 | turn ID | 真实 provider wire | 图像输入 | 结果 |
| --- | --- | ---: | --- | --- |
| 1 | `921c175f327b40fa96b9bbe9156a4298` | 23 次 | 28 部分：23 张用户原图 + 5 张 gen1 预览 | revision 4，preview generation 1；1 张参考图 SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`；CLI进度事件170，实际用量 input/output/reasoning `486428/18603/12533`，cached input `444160`。耗时约687秒。 |
| 2 | `d1fae560acbc4fa58ac7a217455c1ea9` | 6 次 | 2 部分：均为当前 gen2 preview | 纯文字把主体高度 `80 → 85 mm`；revision 5，preview generation 2；CLI进度事件56，用量 `104769/1586/907`，cached input `87680`。 |
| 3 | `b52046c710fa4db0a237da981a06e720` | 7 次 | 4 部分：2 张 gen3 + 2 张 gen4 preview | 纯文字连续两次各增加高度1mm；revision 7，preview generation 4，包围盒 `90 × 36 × 87 mm`；几何/BREP通过，CLI进度事件63，用量 `112012/2098/1435`，cached input `95232`。 |

结果目录分别是 `turns/<turn-id>/`，每轮包含 `result.json`、`agent-observability.jsonl`、CLI stdout/stderr、CAD状态及几何/预览文件。

## 真实请求图片清理

第三轮真实 wire 在 gen3 阶段记录 transport SHA-256 `d6e13b9c2aedfd4ff418a8e58b531d76e6df6bdbfd4ebece32b53c0b23d9958d`。生成 gen4 后，后续两次 provider 请求均报告 `latest_generation=4`、`removed_stale_tool_previews=1`、`kept_tool_previews=1`；发送体只留下当前 gen4 ISO 预览 SHA-256 `4482f9887faacbbea904d70edf59898082fb8fc46fd99cf8b680cc7403225fc2`。这是同一真实 CLI turn 内删除旧 gen3 并保留 gen4 的实测证据。

## 原图跨 CLI 轮次行为

原图文件、reference manifest 和稳定 SHA `53ed87...94553` 在同一持久 session 中继续保留；turn2/turn3 的 `active-turn.json` 仍列有这张图。实际 provider 请求显示，新 CLI 进程使用新的 DSH session，上下文历史以可见文本保存：turn2/turn3 的 provider wire 都没有 `source_kind=user_reference` 图像部分，只在相应预览产生后携带 preview。原图仍可由 MCP 稳定引用读取，但当前行为不会在纯文字续接时自动重新放入 provider 请求体；请求图像统计正确记录为本轮0张用户附件。不要把本地原图保留表述为模型请求持续携带原图。

## Stream 与进度日志

首轮旧逐增量记录有 `19364` 条 `stream_activity`，每个增量同步写JSONL。调整后，turn2和turn3各记录30条采样活动与18/16条phase summary；progress callback分别有56/63条，能以时间间隔报告长推理过程。计数器明确表示 bridge 观测到的chunk事件，不等同provider token usage。首轮通过PowerShell重定向运行时stdout/stderr文件在进程存活期间暂为0字节，退出后分别落盘；后两轮使用操作系统文件重定向，已在运行中看到进度行。

## 回归与静态检查

- Python定向回归：37 tests passed（CLI/输入、stream status、provider usage与推理字段）。
- Node bridge回归：4 tests passed（generation pruning、用户/未知图像保护、usage与图像清点、稀疏activity summary）。
- `node --check ai_modeler/agent/dsh_stream_bridge.mjs` exit 0。
- `git diff --check` exit 0；仅有既存工作树文件的CRLF/LF转换提示，没有空白错误。

详细日志：`input-cli-llm-regressions-final-20261001.txt`、`stream-bridge-node-tests-throttle-20261001.txt`、`stream-bridge-node-check-20261001.txt`、`input-diff-check-final-20261001.txt`。
