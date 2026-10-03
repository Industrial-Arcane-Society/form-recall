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
