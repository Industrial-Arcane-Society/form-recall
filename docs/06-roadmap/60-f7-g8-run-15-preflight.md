# F7 G8 Run15 冷启动前置结果

## 运行结果

Run15 未启动。runner 的单次 CLI 启动请求在进程创建前被执行器拒绝，工具返回 `CreateProcess ... Rejected(...): blocked by policy`。该错误没有提供更具体的策略原因。没有启动 runner 或 DSH，没有发送 provider 请求，也没有可记录的 Run15 gate 结果；本记录不是模型验收报告。

## 已核验的前置项

- 使用当前隔离 worktree 中跟踪的 DSH runtime manifest 和锁文件，准备 ignored `target/f1-runtime-probe`。Node 官方归档 SHA-256 与 manifest 相符；Node `v22.23.3`、DSH `0.2.0-rc.2` 均通过本地版本核对，锁文件安装报告 538 个包完成。
- 原始参考图在已记录路径存在，大小 72,309 bytes，SHA-256 与基线 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553` 一致。未读取、展示或复制图片内容。
- 当前进程的 `DEEPSEEK_API_KEY` 仅核验存在状态；密钥值没有读取或写入 descriptor。公开 provider descriptor 指向 `https://api.deepseek.com/v1`、模型 `deepseek-flash`、推理强度 `low`。
- OCCT runtime manifest、当前 worktree 编译出的 Rust probe/profile 可执行文件均存在。DSH home 与输出目录规划为本轮新建隔离路径。

## 边界与后续

CLI 被拒后没有改用其它执行通道或重试。创建的公开 descriptor 和空输出目录留在 ignored `temp/acceptance/`；执行器也拒绝了清理命令，因此它们被保留。descriptor 只含 provider URL、模型名和推理强度，不含凭据；没有 runner 产生的 trace、payload、thought 或 gate 数据。本次不得推断 v2 projection 的真实运行表现，也不得推断任何门禁通过/失败。

恢复 Run15 需要一个允许启动该本地严格验收 runner 的执行上下文。重新运行前仍须使用新输出目录、同一原图 SHA 和隔离 DSH home；完成后只读取自动 gate 结果与两个固定脱敏 projection，并维持现有 strict gates。

## 安全发布修复（尚未运行真实 DSH）

后续静态审查发现旧 runner 会写出 `tool-trace.json`、带 run/session ID 的完整 strict gate、ACP tool-event 列表，以及 answer/hash/thought 长度等字段。旧的 Run15 CLI 尝试在 CreateProcess 前被拒绝，因此这些旧 runner 输出未在 Run15 产生；此前历史 run 记录不在本次读取范围内。

基于 `1e445f1` 的独立隔离 worktree 中已将发布路径收敛为固定三个文件：`summary.json`（总体状态、固定 gate 名称/状态/reason 计数、白名单 runtime 版本与输入图像字节数/SHA）、`tool-result-diagnostics.jsonl`（ACP terminal update v2 HMAC 聚合和 Cordis binding v1 白名单计数）及 `build-status.json`（结果、退出码、耗时、stdout/stderr 字节数）。raw trace、自由文本原因/evidence error、run/session/request/call ID、工具参数、answer/hash、thought 内容/长度、profile、Rust postcheck 和模型几何文件不进入这些发布文件或控制台。诊断投影现在默认生成，没有 debug/raw dump 开关；strict gate 逻辑保持原样。

为满足 probe/DSH 的工作目录契约，runner 将会话 manifest、stream/native audit、profile 和候选 artifacts 写到系统临时目录的私有 scratch；正常完成、错误退出及常规进程退出都会尝试删除。该 scratch 在运行期间仍是本机临时文件，并非内存文件系统；断电、强制结束进程或删除失败可能留下 scratch。发布层重新构造固定 gate-name allowlist、只输出状态/reason_count；自由文本原因、evidence error、scope 与 runtime 字符串都会丢弃或映射为固定标签。合成 sentinel 回归测试把 ACP/native ID、answer、thought、payload 与参数 marker 注入 scope、gate names/reasons、failure/evidence errors、runtime 及 projections，扫描每个输出文件并确认没有泄漏，同时确认 passed gate 和 v2/v1 projection 保留有效字段：55/55 通过。未启动真实 DSH/provider，也未生成 Run15 gate 结论；待代码审阅后再决定是否重跑。
