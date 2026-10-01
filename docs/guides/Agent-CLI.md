# DSH Agent 命令行建模

`agent` 子命令运行与桌面「Agent 创造模式」相同的 DSH SDK、多模态输入、MCP 工具和本地几何审查流程。它不会调用旧的规则解析器，也不会预置 CADPlan。

## 运行

在项目根目录执行：

```powershell
python -m ai_modeler agent --text "创建一个100×60×20毫米长方体"
```

从 UTF-8、UTF-16 或 GB18030 文本文件读取：

```powershell
python -m ai_modeler agent --text-file .\request.txt
```

输入同一工件的多张参考图：

```powershell
python -m ai_modeler agent --text "按参考图复刻，尺寸未标注请自行估量" `
  --image .\front.png --image .\iso.png
```

纯文字续接已有对话时，复用相同的 `--session-id`；新会话应使用不同 ID。CLI 将持续保存 CAD 状态、原图资产、user turn 清单、DSH 本地会话和每轮的脱敏调用记录。每轮进度写入 stderr，最终结构化 JSON 单独写入 stdout。

```powershell
python -m ai_modeler agent --text "先按图建立候选" `
  --image .\reference.png --session-id nine-hole-replica
python -m ai_modeler agent --text "中排孔径增加10%，其它不变" `
  --session-id nine-hole-replica
```

可以用 `--output-dir` 指定交付目录。每次运行输出到 `turns/<turn-id>/`，其中包括结果 JSON、当前模型文件、预览、几何报告和请求时间线。默认位置为 `artifacts/agent-cli/<session-id>/`。`--offline` 仅在该次进程内关闭网页搜索，不更改 `config.json`。

```powershell
python -m ai_modeler agent --text "创建一个100×60×20毫米长方体" `
  --session-id box-smoke --output-dir .\artifacts\box-smoke --offline
```

## 退出码

- `0`：通过本地几何有效性与计划校验的已接受候选，或成功完成的纯对话；`cli_outcome` 会区分两种情况。
- `3`：Agent、配置或运行错误。stdout 仍包含结构化错误 JSON，stderr 显示实时进度和错误信息。
- `4`：候选已保存，但尚未达到已接受状态。
- `5`：模型可能已生成，但必要交付文件缺失或复制失败。
- `2`：参数或输入错误。

代码 0 的纯对话结果标为 `conversation_completed`，不会声称建模成功。只有 `state=accepted`、计划校验通过、BREP 有效且关键交付文件存在时，模型结果才使用 `accepted_and_verified`。本地 BREP/图像复核结果不代表 CATIA CATPart 已运行或通过验证。

## 请求可观测性

每轮结果会记录 DSH 逻辑请求、真实 provider wire 请求（若 hook 观察到）、图片 source/transport 哈希、推理配置、首工具耗时、实际 usage（若供应商返回）、MCP 工具进度和报告路径。无法从真实事件证明的字段保持 `null` 或 `unknown`，不会通过助手文字长度推算。配置中的 API key 不写入 stdout、结果文件或进度时间线。

当前运行环境中同一个 session ID 不允许同时由多个 CLI 进程操作。进程退出时只关闭 DSH runtime，保留工作目录和会话证据以供后续文字续接。
