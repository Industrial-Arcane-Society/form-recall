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
