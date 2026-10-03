# F7.G8 runner 子进程与 scratch 清理生命周期

日期：2026-10-04。

## 发现

只读审查发现，旧 `boundedExit()` 在等待期限结束后发送 `kill()`，但不等待进程退出；顶层异常处理也只发终止信号便立即退出。此时 runner 会继续 postcheck 或开始删除 scratch，存在子进程仍打开/写入私有运行文件、删除失败的竞态。

## 修复

- 新增 `native_agent_coldstart_lifecycle.mjs`，将子进程退出等待、超时终止、第二个有界退出等待和 scratch 删除结果归并为可测函数。
- 正常退出先等待 DSH/probe 的第一期限；超时后发送终止信号，再等待第二期限。只有确认两个托管进程都退出，且 scratch 删除成功，才把 `scratch_cleanup_status` 记录为 `removed`。
- `uncaughtException` 和 `unhandledRejection` 处理器现在等待托管子进程终止并退出，随后尝试清理 scratch，再写入安全失败摘要。
- 安全摘要和 stdout 只暴露固定枚举 `scratch_cleanup_status`（`removed`、`failed`、`not_created`），不包含目录、进程 ID、错误正文或异常内容。清理失败或仍有子进程未退出会记录 `failed`，并让 runner 以非零状态结束。
- `exit` 同步处理器保留为最后一次 best-effort 清理；断电、强制结束或连续删除失败仍可能留下临时文件，因此这里不是持久清除保证。

## 验收

运行：

```powershell
node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs
```

结果：77/77 通过，其中新增三个合成故障场景：超时后收到退出事件、顶层失败关闭等待并发生 scratch 删除失败、终止后的第二期限届满但子进程仍未退出。删除错误正文和 scratch 路径不会进入返回状态。

另检查 runner、生命周期模块及 publication module 的 Node 语法，并运行 `git diff --check`。未启动 DSH/provider/GUI/CATIA，未执行真实 Run15；因此真实 Windows 子进程退出和真实文件锁条件仍没有运行态证据。

本修复仅收敛 runner 清理边界，不改变 strict gates，也不改变 Run15 `blocked by policy` 事实或 F7/G8 总体验收状态。

## 崩溃残留的启动时保守清扫

后续实现新增 `native_agent_coldstart_scratch.mjs`。runner 在创建本轮 scratch 前，扫描系统 `tmpdir()` 的直接子目录，只考虑精确前缀 `aimodeler-coldstart-private-` 加 64 位十六进制 nonce 的目录。新目录以独占 `mkdir` 创建，并通过同目录临时 marker + rename 原子写入 owner marker；marker 仅含固定 schema、PID、UTC 创建时间和目录 nonce。

清扫器仅在 temp root 与目标路径均可确认不是 symlink/reparse point、marker 格式和目录 nonce 完全匹配、marker 年龄至少 24 小时、PID 探测明确返回 `ESRCH` 时删除。活动 PID 保留；`EPERM`、探测异常及其他非 `ESRCH` 状态均按 owner unknown 保留。无效目录/marker、年轻目录和删除失败均只增加固定分类计数，不返回/打印路径、marker 内容、PID 或错误正文。`summary.json` 和 stdout 仅记录清扫 status 与计数；这些计数不影响 strict gate，也不把清扫成功当作视觉验收。

新增合成测试覆盖死 PID 清理、活 PID 与未知 PID保留、marker 年龄门槛、错误 marker、非前缀/嵌套目录不扫描、symlink 目标和 symlink root 拒绝、删除失败不泄露敏感路径/错误。运行验证：`node --test src/apps/desktop/src-tauri/examples/native_agent_coldstart_gates.test.mjs`：83/83 通过；语法检查覆盖 runner、清扫器和 publication module；`git diff --check` 通过。没有启动 GUI、DSH 或 provider。

边界：该逻辑只能在后续 runner 启动时清扫仍可访问且 marker 完整的过期目录；活动/状态不明 PID、marker 损坏、清扫失败及尚无后续启动的残留都会保留。它不是可靠擦除保证，不弥补真实模型视觉理解、返修或新版复核验收。
