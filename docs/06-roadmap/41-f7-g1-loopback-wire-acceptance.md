# F7.G1 本机 loopback 主模型图片 wire 验收

新增 `native_agent_loopback_wire_acceptance.mjs`，用于验证固定 DSH ACP Runtime 从 `session/prompt` 接收图片后，主 DSH provider 发出的 OpenAI-compatible HTTP body 是否仍包含同一张图。该路径不读取生产 provider 配置，不接受或读取真实 API key，也不改变生产 runner 的默认行为。

## 运行

需准备官方固定 Runtime 的完整目录：Node `v22.23.3`、DSH/ACP `0.2.0-rc.2`、ACP SDK `1.4.0`。输出目录必须尚不存在，且其父目录已存在。命令在 Windows PowerShell 中：

```powershell
$repo = (Get-Location).Path
$image = 'temp/evidence-archive/artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png'
$runtime = 'src/apps/desktop/src-tauri/target/f1-runtime-probe'
$out = 'temp/acceptance/f7-g1-loopback-wire-20261003-run04'
& "$runtime/node.exe" 'src/apps/desktop/src-tauri/examples/native_agent_loopback_wire_acceptance.mjs' $repo $image $runtime $out
```

脚本绑定本机随机端口 `127.0.0.1`，只在该 listener 上响应 `/v1/models` 和 `/v1/chat/completions`。子进程只获得最小化环境和一个仅用于本机请求的 dummy key；profile 把唯一 provider/model 指向 loopback，关闭外部工具、遥测和全局附件服务。mock 不保存请求原文或图片，只在内存中解析 image data URI，并落盘一行脱敏摘要：HTTP 方法与路径、model、图片数量、图片 MIME、解码字节数和 SHA-256。mock 返回合法的 Chat Completions SSE 响应。

成功时 `summary.json` 为 `status=passed`，`mock-wire-summary.json` 中恰有一个 `/v1/chat/completions` 主模型请求。脚本会断言以下 SHA 与长度完全相同：原始输入文件、`dsh_stream_bridge` 观察到的 `llm/stream` user-reference image、loopback 收到的 wire data URI 解码结果。它也会核对固定 Runtime 的实际 Node、DSH、ACP 和 SDK 版本；任何额外模型/路径请求、缺图、图片 SHA 或长度不一致、DSH 报错都会令命令失败。

定向 helper 测试：

```powershell
& "$runtime/node.exe" --test 'src/apps/desktop/src-tauri/examples/native_agent_loopback_wire_helpers.test.mjs'
```

2026-10-03 本机 probe run02：4/4 helper tests 通过；完整 CLI 返回 `status=passed`，一个主模型 Chat Completions 请求，输入、DSH stream boundary、mock wire 都为 SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553` / 72,309 bytes。固定 runtime 为 Node `v22.23.3`、DSH/ACP `0.2.0-rc.2`、ACP SDK `1.4.0`；DSH exit 0、stderr 0。脱敏摘要在 `temp/acceptance/f7-g1-loopback-wire-20261003-run02/`，`summary.json` SHA-256 `22cfbb900154e2a7f8395bffb99d7115c546ef297899d6c9327bf3f0991d012f`，`mock-wire-summary.json` SHA-256 `b4d3258c2e932a97e4259b137ef57742882ad67acc84eed588f90a1102a32c76`。这些是本地请求序列化与 loopback 接收证据，不代表远端 provider/模型理解。

最终 CLI 代码再运行 run03，SHA 比对结果相同；目录 `temp/acceptance/f7-g1-loopback-wire-20261003-run03/` 的 `summary.json` SHA-256 为 `546ffd10471cf685d66c52f95b3f3914e6c3159f429842c7898bbeda42654118`，`mock-wire-summary.json` SHA-256 仍为 `b4d3258c2e932a97e4259b137ef57742882ad67acc84eed588f90a1102a32c76`。

## 证据边界

该验收证明指定 DSH ACP Runtime 的主 provider 序列化和 HTTP 请求体中含有与输入及 DSH stream boundary 一致的图像字节，且测试时没有访问真实模型 endpoint。它不证明远端 provider 接收/保留了图片，也不证明任何模型理解了图片；后者需要另行执行经授权的真实 provider 验收。专用 `rust_native_vision` 请求不参与此 CLI 或其 SHA 判定。
