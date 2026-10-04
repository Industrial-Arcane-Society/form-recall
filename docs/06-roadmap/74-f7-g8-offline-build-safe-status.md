# F7/G8 离线构建失败安全状态发布

## 背景

Run20 的安全摘要把 fatal 阶段归为 `offline_build`，但该路径在 runner 调用同步 Cargo build 后立即抛错。正常的 `build-status.json` 只在后续完整结果发布时生成，fatal handler 只发布 `summary.json`，因此构建失败时缺少可审计的构建状态。这个历史现象不能据此确定 Run20 的具体 Cargo 结果；Run20 没有 `build-status.json`，其构建结果仍是 unknown。

## 修复

- `native_agent_coldstart_build.mjs` 将离线构建同步调用封装在 `runOfflineCargoBuild()` 内。它不返回子进程 stdout、stderr 或异常对象，只返回固定字段：`status`、`exit_code`、`elapsed_ms`、`stdout_bytes`、`stderr_bytes`。
- Cargo 非零退出、`spawnSync` 返回 error，或调用同步抛错时，helper 在返回 runner fatal path 前写出 `build-status.json`。异常对象不被读取；抛错情况下 exit code 为 `null`、字节计数为 0。
- `native_agent_coldstart_publication.mjs` 的 `sanitizeSafeBuildStatus()` 将状态限制到 `passed` / `failed`，并验证数值字段；`writeSafeBuildStatus()` 使用 `wx` 防止覆盖已有证据和权限模式 `0600`。完整成功路径继续经 `writeSafeAcceptanceArtifacts()` 使用同一 sanitizer 发布构建状态。
- runner 仅在 helper 返回 `passed` 后继续到 native probe；失败时才进入既有泛化 fatal summary 发布。不会增加自由文本错误或额外构建日志。

## 回归验证

隐私回归覆盖 Cargo 非零退出、`spawnSync` 返回带 error 的结果、`spawnSync` 同步抛错和成功结果。失败情形写出安全状态，测试检查文件只含五个白名单字段，stdout/stderr 字节数按 UTF-8 / Buffer 长度计数，exit code 不可得时为 `null`，并断言 stdout、stderr、异常正文中的 sentinel 不出现在文件中。成功路径由既有完整发布路径产出构建状态。

执行：

```powershell
& '<pinned Node 22.23.3>' --check 'src/apps/desktop/src-tauri/examples/native_agent_coldstart_build.mjs'
& '<pinned Node 22.23.3>' --check 'src/apps/desktop/src-tauri/examples/native_agent_coldstart_publication.mjs'
& '<pinned Node 22.23.3>' --check 'src/apps/desktop/src-tauri/examples/native_agent_coldstart_acceptance.mjs'
& '<pinned Node 22.23.3>' --test 'src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs'
```

以上静态语法检查通过，`native_agent_coldstart_gates.test.mjs` 通过 `92/92`。只执行了本地 Node 检查与合成测试；没有启动 DSH/CATIA、读取密钥、重跑 Run20 或检验真实 Cargo 故障现场。Run20 仍缺失的状态文件不会被回填。F7/G8 真实冷启动验收仍需在后续单次运行中确认。总 TODO 中的 Run20 “静态检查 offline_build fatal path”后续项可在集成时替换为本修复与仍待真实复跑的验收边界；本提交不改总 Todo，避免与其它运行记录编辑交叉。
