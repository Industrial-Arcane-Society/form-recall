# 桌面建模 Agent：DeepSeek Harness SDK 接入

## 运行方式

界面使用 Python SDK 启动本地 DSH runtime。安装脚本在项目本地前缀安装 `@deepseek-ai/dsh@0.2.0-rc.2`，并由 Python SDK 的公开 `dsh_bin` 参数指向该 CLI shim；会话只使用独立的 DSH home，不读写用户全局 profile。当前 PyPI 尚无匹配的 `deepseek-harness-sdk` / runtime-bin 0.2.0rc2 wheel，因此 SDK JSON-RPC 启动适配器仍为 0.1.5rc1。已验证 RC2 initialize 和 25 项 Cordis 工具注册；首次真实图像请求发现 RC2 的附件读取目标已改为 `{width, height, maxBytes}`，项目 overlay 已按该接口适配，并通过同一原图的无模型 request-image smoke。RC2 原生模型调用结果需由修复后的独立冷启动验收确认。项目使用 `ai_modeler/agent/dsh_attachment_store.mjs` 提供每聊天独立的图片附件服务，避免会话附件落入用户全局存储。

安装 Agent 运行时：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/01_安装依赖.ps1
```

在“工具 → AI 大模型设置”填写接口地址、模型名和 API Key。运行时按该配置更新完整 profile 已有的 `llm-pi-ai` Provider route，默认声明文字和图片输入；根据 DeepSeek Responses、通用 OpenAI-compatible、SiliconFlow Kimi 等端点设置适配协议与思考预算。路由上下文窗口按 1,048,576 配置，思考强度沿用界面选择。网关是否真正支持图像、工具及相应思考参数仍由服务端能力决定；服务端拒绝时显示错误，不回退到旧文本建模流程。

## 会话与数据

每个聊天对应一个 DSH session，跨用户消息持续保存上下文；由 DSH 负责选择是否调用工具、调用顺序和循环次数。Agent 的系统提示词位于 `prompts/dsh_agent/system.md`，实时流事件由 `ai_modeler/agent/dsh_stream_bridge.mjs` 转发到桌面思考面板。SDK runtime 与会话文件位于 `%LOCALAPPDATA%/AI Modeler/dsh/sessions/<session-id>/dsh-home`，不修改已有的全局 DSH 安装。

新建聊天或关闭应用时，程序停止当前 SDK runtime 并清除该聊天的 DSH 会话存储，其中包括旧对话里的参考图，避免长期堆积。已保存的模型、网格和报告保留在同一个聊天目录的 `output` 子目录；每个会话的工作预览固定覆盖 `current-preview.png`，不会为每次视图检查另存一份临时图。知识库视觉样例板仅作为初始请求中的一张附件传入，临时 PNG 编码后立即删除；用户参考图保留在会话资产库，Agent 按需调用原生图像工具查看。

## Agent 工具

`ai_modeler/agent/dsh_native_tools.mjs` 是原生 Cordis 插件，在 DSH Agent 工具注册表直接注册 CADPlan 和网页搜索能力。工具实现经 `ai_modeler/agent/dsh_tool_worker.py` 的应用私有 JSONL/stdin 通道调用 `dsh_tool_backend.py`；几何、CADPlan 和知识库业务位于协议中立的 `dsh_tool_domain.py`。DSH Agent 路径没有 MCP client、server、消息协议、fallback 或相关模块引用。原有快速模式和 CATIA MCP 保持独立。

- `cad_create_model`、`cad_add_feature`、`cad_update_feature`、`cad_remove_feature`：按增量维护 CADPlan；数值参数须附来源。
- `cad_get_state`、`cad_get_feature_spec`、`cad_validate_model`：读取特征树、按需查特征参数并验证计划。
- `cad_preview_model`：生成当前 CADPlan 的单视图或多视图；预览 PNG 通过 DSH 原生 attachment content 回到多模态模型，并同步更新右侧预览。
- `cad_get_reference_image`：读取原始参考图或指定 ROI；图像字节通过同一原生 attachment 路径回到模型。
- `cad_record_visual_review`、`cad_finish_model`：复核记录绑定模型 revision、视图和 preview generation；同一几何版本的多视图记录会累积，改变几何后整体失效。确认无未解决差异时保存为完成候选；确有图像歧义时可逐项确认并保存为待确认候选，报告会保留这些差异。
- `web_search`、`web_open_result`：联网设置开启时按模型判断使用。普通按图复刻不强制联网。

启动 overlay 关闭 DSH 自带的 Bash、PowerShell、文件系统、Skills、子 Agent、任务编排和通用 Web 工具；只向模型开放本项目通过 Cordis 注册的原生工具，联网搜索也受应用联网开关控制。原图在进入 DSH 前会用 Pillow 规范化为 PNG/JPEG，限制在 16 MP 和 4 MiB 内；小于预算的 PNG/JPEG 保持原字节。附件服务用内容哈希存储和校验图片，并将它们保存在该聊天 DSH home 中。工具图片结果先调用 DSH 附件服务保存，再以 `{type:'image', attachment: ImageAttachmentRef}` 作为原生工具内容返回。Agent 不生成或执行任意 Python、Shell、VBScript、CATScript 或宏。会话日志上传、遥测和本地 Agent 指令注入一并关闭。CADPlan 增量状态由本地服务维护；每次几何修改都会使旧版本的预览与复核失效，需用新版本证据核对。外部网页结果只作为不可信事实来源，不能覆盖用户提供的约束。

## 交付边界

本流程保存可编辑的 `.aimodel`、STL、本地预览和几何报告。预览使用 AI Modeler 本地网格内核；本流程不自动启动 CATIA、不生成 CATPart，也不把模型视觉复核描述成 CATIA B-Rep 验证。CATIA 导出继续使用现有 SDK / MCP 手动通道。

```text
%LOCALAPPDATA%/AI Modeler/dsh/sessions/<session-id>/
  cad-state.json
  current-preview.png
  output/
    candidate.aimodel
    candidate.stl
    preview.png
    geometry-report.json
```

若本轮没有通过完成门禁，右侧仍可查看最后一个有效候选，报告中保留当前验证、复核和未解决项；不会伪称视觉复核已通过。
