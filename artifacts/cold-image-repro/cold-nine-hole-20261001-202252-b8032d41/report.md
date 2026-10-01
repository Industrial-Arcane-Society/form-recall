# 冷启动图像复刻验收报告

## 结论

**失败 / 未完成。** 这次真实 DSH Agent CLI 冷启动收到了 provider 与 MCP 的真实调用，也创建并修改了本地 CAD 状态；但 Agent 在未产生最终候选时持续进入 reasoning 请求。按任务负责人指令，仅结束本次会话进程树。进程结束前 Agent 自报 `cad_validate_model` 校验已完成，但最终 stdout 结构化结果为空；CAD 状态为 `finished=false`、无 accepted candidate、无导出路径。不得把局部 BREP 校验视为最终模型验收。

## 输入、命令与运行时

- 原始输入图：`artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`，SHA-256 `53ed87fadf8cca869da9216276e13c845424c6906b3de517d5cca972db94553`，494×544、72,309 bytes。验收目录中保留了同一图片副本。
- 提示词：`请按参考图复刻这个工件；图中没有标注的尺寸请按图像比例估算，标明估算来源，生成本地模型`
- 完整命令：

```powershell
python -m ai_modeler agent --text "请按参考图复刻这个工件；图中没有标注的尺寸请按图像比例估算，标明估算来源，生成本地模型" --image "artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png" --session-id "cold-nine-hole-20261001-202252-b8032d41" --output-dir "C:\Users\lycohana\Desktop\code\AI-_-\artifacts\cold-image-repro\cold-nine-hole-20261001-202252-b8032d41"
```

- 运行时间：开始 `2026-10-01 20:22:52.084 +08:00`；强制终止记为 `2026-10-01 20:40:03.112 +08:00`；耗时约 17 分 11 秒。终止确认时刻 `20:40:27 +08:00`。
- CLI 退出码：`-1`（被外部 `Stop-Process` 终止后记录；不是 CLI 的正常退出码）。CLI help 未提供启动/请求超时参数。
- Session ID：`cold-nine-hole-20261001-202252-b8032d41`。Turn ID：`eaed097bd19a4fde82555317706ef323`。DSH session ID：`cold-nine-hole-20261001-202252-b8032d41-dsh-aa8d02550f2d`。
- Provider：SiliconFlow；模型：`moonshotai/Kimi-K2.7-Code`；reasoning effort：`low`。
- 进程已确认退出：PID 23464（CLI）、41204（DSH runtime）、27200（MCP server）。

## 真实调用计数

本次封存的 `agent-observability.jsonl` 记录：

- 38 个 DSH request start；39 个 provider wire request；37 个 DSH request complete、38 个 wire response complete。终止时仍有 1 个逻辑请求与对应 wire 请求未完成，最后事件为 reasoning stream activity。
- 53 次 MCP 工具调用，均有 tool complete 事件：`cad_create_model` 2、`cad_get_state` 2、`cad_inspect_references` 1、`cad_get_feature_spec` 2、`cad_add_hole_array` 5、`cad_add_face_recess` 1、`cad_add_feature` 3、`cad_update_feature` 14、`cad_remove_feature` 16、`cad_preview_model` 5、`cad_validate_model` 1、`cad_get_reference_image` 1。
- 39 条 wire request 全部保留了原始用户图作为 user-reference image（哈希同上）。payload image 数量分布为 1 张×8、2 张×15、3 张×4、4 张×9、5 张×3；后续请求还附带工具生成的预览图。
- 38 条 wire 请求标明 reasoning enabled，1 条未给出该字段。37 条 usage event 累计记录 input_tokens 116,707、output_tokens 21,620；原始 `total_tokens` 字段相加为 1,634,007，与 input/output 合计不一致，因此单列原始值、不把它当作可信总量。

## 最终 CAD 状态

截至最后保存的 CAD state：revision 32，`finished=false`，`finish_state` 为空，`output_paths={}`，没有 `last_valid_candidate`、`best_evidence_candidate` 或 `best_candidate`。最终 plan 有 6 个特征：

| ID | 类型 / 操作 | Agent 参数及来源摘要 |
|---|---|---|
| `base_box` | box / add | 100×50×80 mm；三个方向均标为 `image_estimate`，依据图中整体宽、厚、高比例估算。 |
| `front_slot` | box / cut | 30×35.001×65 mm；槽宽/高度及约 15 mm 顶壁标为图像比例估算；切深和中心含 derived 计算。 |
| `horz_hole_1..3` | hole / cut | Ø12 mm，X 轴贯穿，位置 X=-50、Y=7.5、Z=20/40/60；孔径为图像估算，坐标为转换/派生值。 |
| `back_blind_1` | hole / cut | Ø12 mm，轴 Y，点 (0,-24.5,20)，深度 15.5 mm；孔径和盲孔意图为图像估算，点位/深度含派生值。此特征没有通过切除校验。 |

Agent 曾加入 back wall hole/cylinder features，后又自行删除；这些已不在 final plan。此状态保留三条横向通孔和一条反复失败的盲孔定义，不宣称目标孔布局已完成。

### 局部几何证据

revision 29 / preview generation 5 的 partial preview 为 `candidate_partial_not_accepted`，因 `back_blind_1` 的 `cut_no_material_change` 失败。其失败前的局部 BREP 检查记录：`brep_valid=true`、单一闭合 solid、0 free edges、无自交检查命中；bbox 为 `[-50,-25,0]` 到 `[50,25,80]` mm（100×50×80 mm），volume 307,864.5129 mm³。该部分只记录了 base box、recess 与三条横向孔被应用；blind hole 未应用。状态为 `needs_review`、`accepted=false`。这是部分模型的 BREP 数据，不是最终候选的验收结果。

失败历史中 `back_blind_1` 相同 `cut_no_material_change` 已重复 4 次。最终 stderr 显示 rev32 的 `cad_validate_model` 工具提示“校验已完成；未启动 CATIA”，但没有 stdout JSON 或保存下来的最终 BREP 报告可证明其细项、bbox 或孔布局。没有 CATIA 运行证据。

## 交付物与 SHA-256

- AIMODEL：未生成；`output_paths` 为空。
- STL：未生成。
- 可用 preview PNG：未生成/未保留。最后日志提到的尝试路径为 `C:\Users\lycohana\AppData\Local\AI Modeler\dsh\sessions\cold-nine-hole-20261001-202252-b8032d41\previews\r29-g5-iso.png`；关停时 session preview 目录没有 PNG，因此没有 preview SHA-256。
- stdout：`stdout.json` 0 bytes，无结构化最终 JSON。
- stderr：`stderr.log` 28,625 bytes，含实时进度及最后校验提示。
- 逐文件 SHA-256 见 `evidence/SHA256SUMS.txt`。

## 未解决项

1. 最终 provider 请求未完成，CLI 未产生最终结构化结果。
2. 没有最终 accepted candidate、AIMODEL、STL 或 preview 文件。
3. 最后 plan 中盲孔特征无法切除；此前候选重复 `cut_no_material_change`，Agent 采用删除特征回退，最终模型未涵盖已尝试的所有孔特征。
4. 当前 plan 的估算尺寸虽写明图像比例来源，但无标注尺寸可用于独立定标；孔布局与完整性未获最终验收。
5. 最终 `cad_validate_model` 仅在 stderr 有“校验已完成”的进度文本，没有对应的最终报告文件；因此最终 BREP、bbox 和孔布局状态不可确认。

## 证据目录

`evidence/` 保留最终 observability JSONL、CAD state、actions 摘录、feature/provenance 摘录、turn/session 元数据、进程快照、终止确认、原始输入图副本、CLI stdout/stderr 和 invocation 记录。原始 CLI 输出文件也保留在本目录根部。

