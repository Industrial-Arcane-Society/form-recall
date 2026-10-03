# F7.G8：严格冷启动 CLI 验收报告

## 本次改动与证据边界

`native_agent_coldstart_acceptance.mjs` 的总状态只有 `passed` / `failed`。只有下表所有 gate 通过，进程才退出 0；ACP 结束、图片成功传输或宿主单独声称 `accepted=true` 都不能单独产生通过。发布目录只保留安全 gate 摘要、两种 allowlist aggregate/HMAC projection 和离线 build 状态。原始 ACP event、临时 ID、工具参数、answer/thought 和模型候选只用于当前进程内 gate 计算；运行时所需 scratch 在正常结束时删除。

本次只运行合成 fixtures、Node 语法检查和本地 loopback 工具桥测试，没有发起真实模型请求。36 项定向测试通过不表示原图建模、视觉理解、孔计量或 CATIA 已通过。历史 `temp/acceptance/f7-production-image-20261003-run01/` 和对应负样本文档保留不变；下一次真实运行必须新建目录。

## 必须同时通过的 gate

| Gate | 必需证据与绑定 | 拒收示例 |
| --- | --- | --- |
| `run_scope` | 本次 UUID run/turn、ACP session、native bridge session；单图 manifest 与单个用户 turn 绑定同一原图 ID/SHA | 缺 session、旧 turn、引用 manifest 不一致 |
| `dsh` | `session/prompt` 与 close 成功且 DSH 正常退出 0 | 超时、取消、非零退出或强制终止 |
| `trace_integrity` | ACP stdout 可解析；当前 `stream-events.jsonl` 和 `native-tool-audit.jsonl` 存在且每条非空行有效 | 损坏日志不得跳过后继续报通过 |
| `original_image` | 原图先验固定 SHA/72,309 字节，运行前后 SHA 不变 | 改图、压缩、裁剪或替换 |
| `main_image_wire` | 当前 run 时间窗/turn 下 `node_global_fetch` 实际主模型 request；model、provider host、原图 source/transport SHA/bytes/reference ID 一致；**同一 request_id** 的 HTTP 200 与成功完整响应 | 图片 request A 加上成功 response B、只有专用视觉请求、响应 failed/incomplete、输出长度截断 |
| `cordis_cad_tools` | schema/registration 有效；至少一项真实 CAD 调用；本地代理观察到认证正确的 run/session/turn/native UUID；同 UUID/tool 的 Rust 成功审计；当前 ACP session 中独立完成事件数量足够 | 只有 actions、直接 CLI 调用、foreign session、未知 UUID、缺审计、任一 native 工具被拒或 ACP failed |
| `current_geometry` | 结束后本地 Rust `cad_get_state` 重新计算当前 signature；state/preview/local checks/finish/latest review 的 revision/fingerprint 相同；完整 Rust/OCCT 有效单实体，host attestation 和 gate geometry 通过 | stale preview/finish、部分候选、旧签名或自报检查 |
| `artifacts` | 当前 cadplan、geometry report、preview、STL、GLB、aimodel 都是当前新目录内非空文件（含 realpath 检查）；plan/report 等于当前 state，preview SHA 相同 | 引用历史目录、文件缺失、报告或预览不同步 |
| `holes` | finish 和 review gate 都确认完整；含 hole 时后端 `hole_measurement_complete` 与 `hole_measurements_verified` 都为 true | 宿主 claimed complete 但后端未计量、孔不完整 |
| `visual` | 当前完整引用覆盖、有效 independent review pair、原图 source ID/SHA、inventory valid、无 mismatch；finish visual 等于 Rust 当前视觉摘要 | unmapped evidence、旧 review、覆盖冲突、无有效配对 |
| `finish` | 当前 `finish_summary.accepted=true`、finish state accepted、native review gate decision accept；construction complete、无 open/unresolved finding、必要外部事实无失败/未知 | accepted false、finding 未关闭、事实未知 |

ACP tool ID 与 native 工具 UUID 并不相同。runner 的透明 loopback 代理记录真实 native UUID，然后精确联结 `native-tool-audit.jsonl`；代理只保存 ID、tool、认证 scope、时间和成功状态，不保存 token、arguments、请求/响应正文。直接本地 `cad_get_state` 的 `probe-*` 审计明确不计为 DSH 工具成功。

Rust postcheck 不调用 provider，也不补造 finish/review；它用于重新计算当前状态。报告中 `status_scope=single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance`，表示通过严格 CLI 宿主证据 gate；仍需人工对照原图和预览，独立视觉质量与 CATIA 验收尚未由此命令完成。

## 运行前确认的非秘密状态

1. 当前 branch/worktree、HEAD 和 dirty 路径符合预期；不要把本地 dirty 运行报告当成 clean release。
2. pinned runtime 是 Node `22.23.3` / DSH `0.2.0-rc.2`；OCCT manifest 和本机二进制依赖存在；Cargo offline 依赖齐备。
3. 原图为 `temp/evidence-archive/artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png`，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，72,309 字节。
4. 使用不含 `api_key` 的公开 provider descriptor；由操作者提前在当前进程环境提供 `AIMODELER_LLM_API_KEY`（只检查存在，不输出值）。runner 保留兼容旧 provider JSON 的能力，但新验收命令应使用 keyless descriptor。
5. 本 runner 按 descriptor 的单个活动 provider 构造主模型和视觉 provider；不覆盖桌面完整的 stage provider overrides、AgentHost watchdog/active-model 同步、多图、多轮、GUI、安装或 CATIA。公开 descriptor 的 provider/model/reasoning 必须由操作者确认是本轮要验收的设置。

runner 在发起模型请求前，自动对当前 checkout 执行以下离线重建，失败即停止；不会使用未经当前 Cargo 增量构建确认的旧 examples。构建不调用模型 API，写入 Cargo target。

```powershell
cargo build --manifest-path "$repo/src/apps/desktop/src-tauri/Cargo.toml" --example native_read_probe --example native_agent_profile --offline
```

`build-status.json` 只记录结果/退出码/耗时/输出字节数。summary 只记录总体状态、固定 gate 名称/状态/reason 数、匹配固定表的 runtime 版本与原图摘要；不包含自由文本原因、evidence error、run/session/request/call ID、answer/hash、thought 内容/长度、参数或 profile。未知 scope/runtime/model status 会映射到固定标签。

## 审阅后由操作者执行的真实命令

以下脚本会发起真实 API 调用，**本次未执行**。descriptor 中的公开设置复现 run01 的活动模型；运行前按本轮选定设置调整公开值。环境中凭据由操作者预先设置，脚本不打印或从配置读取凭据值。

```powershell
$repo = 'C:\Users\lycohana\.codex\worktrees\f1-acp-provider\AI-_-'
$runtime = Join-Path $repo 'src/apps/desktop/src-tauri/target/f1-runtime-probe'
$node = Join-Path $runtime 'node/node.exe'
$runner = Join-Path $repo 'src/apps/desktop/src-tauri/examples/native_agent_coldstart_acceptance.mjs'
$image = Join-Path $repo 'temp/evidence-archive/artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png'
if (!(Test-Path Env:AIMODELER_LLM_API_KEY)) { throw '请先在当前进程环境提供凭据；不要打印值。' }
if ((Get-Item -LiteralPath $image).Length -ne 72309) { throw '原图字节数不符。' }
if ((Get-FileHash -LiteralPath $image -Algorithm SHA256).Hash.ToLowerInvariant() -ne '53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553') { throw '原图 SHA 不符。' }
$tag = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N')
$output = Join-Path $repo ('temp/acceptance/f7-g8-strict-' + $tag)
$descriptor = Join-Path $repo ('temp/acceptance/f7-g8-provider-public-' + $tag + '.json')
if (Test-Path -LiteralPath $output) { throw '必须使用不存在的新输出目录。' }
if (Test-Path -LiteralPath $descriptor) { throw '公开 descriptor 也不可覆盖。' }
@{
  base_url = 'https://api.siliconflow.cn/v1'
  model = 'moonshotai/Kimi-K2.7-Code'
  reasoning_effort = 'low'
  dsh_output_cap = 32768
  dsh_context_window = 256000
} | ConvertTo-Json | Set-Content -LiteralPath $descriptor -Encoding utf8
& $node $runner $repo $runtime $output $descriptor $image
$resultCode = $LASTEXITCODE
$summary = Get-Content -LiteralPath (Join-Path $output 'summary.json') -Raw | ConvertFrom-Json
[pscustomobject]@{
  exit_code = $resultCode
  status = $summary.status
  transport_status = $summary.transport_status
  model_status = $summary.model_status
  failure_reason_count = $summary.strict_gates.failure_reason_count
  evidence_error_count = $summary.strict_gates.evidence_error_count
  archive = $output
}
```

不向 runner 提供孔数、尺寸、预置计划或答案；保留默认的图像复刻用户文字。既有输出目录会被 runner 拒绝，拒绝点在创建 run 目录之前；不能将 run01 当成重跑输出目录。真实运行可能需要多次主模型推理、专用参考图 observation、原图/预览视觉 review、外部 web 事实请求，均可能消耗真实 API/网络额度；重试、修复和 context continuation 会增加调用次数。这里只使用一次 ACP 用户 prompt，不代表 provider 只调用一次。

## 运行后查看的安全证据

- 只检查 exit code、`summary.json` 的状态与各 gate 状态/固定原因，及 `tool-result-diagnostics.jsonl` 的两个固定 projection。不能只看 transport 状态或 Agent 最终答复。
- 输出目录只包含 `summary.json`、`tool-result-diagnostics.jsonl` 和 `build-status.json`。runner 不提供 raw trace、tool bridge calls、Rust postcheck 或 CAD 文件 dump。
- gate 通过表示本轮 host evidence 满足严格 CLI 条件；候选几何和临时会话在正常结束时随私有 scratch 一并删除，视觉质量和 CATIA 仍须单独验收。
- 人工对照原图、front/iso 和必要其它视图，核对孔实例数量、孔列、孔轴、凹槽和外轮廓；这是严格 CLI gate 之外的视觉质量证据。不得用模型自报相似度替代该检查。
- 不整份打印 profile、provider/DSH 原始日志或配置。异常处理只输出固定失败代码，不回显错误正文。scratch 位于系统临时目录，正常退出时删除；异常断电或强制终止可能阻止清理，需在允许运行前考虑这一限制。

## 离线定向检查

```powershell
node --check src/apps/desktop/src-tauri/examples/native_agent_coldstart_acceptance.mjs
node --check src/apps/desktop/src-tauri/examples/native_agent_coldstart_publication.mjs
node --check src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.mjs
node --check src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs
node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs
```

新增 table-driven predicate flip fixtures：visual coverage 6 项、original-reference binding 6 项、host finish 7 项；每项先确认合成完整基线通过，再单独翻转/破坏谓词并断言 gate 严格失败。该测试覆盖 Node gate evaluator 条件，不代表真实视觉、finish 或 F7 通过。其余 fixtures 包含完整合成阳性及 request 错配、缺成功完整响应、旧 turn、错误 provider、原图变化、漏工具、只有 actions、native/ACP 拒绝、外来 session/UUID、直接 CLI 冒充 DSH、DSH 非零、accepted=false、geometry stale、holes incomplete、视觉不完整/旧摘要、无效 attestation、缺 artifact/字段、日志损坏等阴性。新增 publication sentinel fixture 检查输出目录所有文件都不含 ACP/native ID、answer、thought、payload 与参数 sentinel，并确认 gate 和投影 schema/内容仍有效。文件路径检查和 loopback 代理另有测试；代理测试确认不会把 dummy token 或正文 marker 写入审计。
