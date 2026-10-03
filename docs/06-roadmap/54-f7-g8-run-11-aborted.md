# F7 G8 Run11 诊断记录

## 执行状态

- 目的：验证 `native_cad_tools.rs` 的 Cordis 图片结果 `mediaType` 修复对原图获取、预览附件与 ACP/Cordis 门禁的影响。
- 基线工作树：`97778c66b0d99dd6cbe5015731ca1aeee8fb7180`。修复提交 `fb045fd` 已在基线历史中（为 `97778c6` 的祖先），因此隔离 worktree 的 cherry-pick 为空；执行时源码已包含修复。
- 单次严格 runner 命令形式：`node native_agent_coldstart_acceptance.mjs --tool-result-diagnostics REPO PINNED_RUNTIME FRESH_OUTPUT PROVIDER_JSON`。runner 进程结束，exit code `1`；未再次运行。
- 唯一输出目录：`temp/acceptance/f7-g8-run11-20261003-media-type-97778c6-3f55acb76b7d4dadb0e9b68c6e6f451e`。runner 未在其中留下 `run-metadata.json`、`strict-gates.json` 或 `tool-result-diagnostics.jsonl`，所以门禁与工具调用结果均不可核实。此次执行不产生可用的视觉或 ACP 验收结论。
- 没有读取 assistant 正文、thought、transcript、raw tool trace、provider response、工具参数或图像数据。

## 输入与环境预检

- 原图：`user-nine-hole-recess-iso.png`，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。
- staging 的 `source_manifest_sha256` 与当前 f4-occt manifest SHA-256 相同：`39402baa4a84d3d8903e1ea97d8085e78ab047b75a568d4bb50617031a2f9649`；`RUNTIME-INFORMATION.txt` 存在。
- Runtime：Node `v22.23.3`；DSH `0.2.0-rc.2`。
- Provider 配置来源：本项目内置 DeepSeek 模板（`deepseek` / `deepseek-flash` / effort `low`）和当前进程中的 `DEEPSEEK_API_KEY`。这不是对当前桌面用户设置文件的声明；配置文件未包含 API key。
- Run11 没有生成运行元数据，prompt/schema SHA、wire 请求及是否进入 provider 阶段均无本次运行证据。

## 门禁与发现

| 项目 | 结果 |
|---|---|
| runner | exit 1；未产出结构化运行证据 |
| image validation | 未核实 |
| `attachment_save` | 未核实 |
| Cordis 工具结果 | 未核实 |
| ACP 结果门禁 | 未核实 |
| 几何与视觉门禁 | 未核实 |
| strict gate 总结 | 不可用；不能视为通过或失败的模型验收结果 |

## 失败归因与限制

本次启动命令在调用 runner 前用 `New-Item` 预先创建了 output 目录。runner 第 44 行检查到目录已存在后立即抛错；`archiveDir` 到第 47 行才设置，因此不会写出 `preflight-failure.json`。源码顺序证明该退出早于原图读取（第 53–67 行）、Node/DSH 检查与 Cargo build（第 73–91 行）、Rust probe、profile 和 provider 请求；所以 Run11 没有读取图像，也没有发出 provider 请求。命令的 stdout/stderr 被丢弃，但实际命令行和 runner 控制流足以定位此问题。Run11 对 `mediaType` 修复的实测结论仍是“未验证”，它不是一条模型样本。

下一次有效运行使用一个不存在的唯一 output 路径，只保证其父目录存在并让 runner 自己创建 output；先通过 runner 语法/usage检查，并只读取 metadata、gates 与白名单工具诊断。禁止把本目录复用成运行输出，也禁止据此推断图片附件成功。
