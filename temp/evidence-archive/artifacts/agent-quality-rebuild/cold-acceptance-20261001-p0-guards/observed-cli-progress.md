# CLI 可见进度摘录

本文件依据监督端接收的 `python -m ai_modeler agent` 标准输出事件摘录，不是原始完整 stdout/stderr。CLI 的 `[... +Ns]` 是本次 run tracker 相对秒。

- `+0.1s`：本地工件知识库无可靠文字命中。
- `+0.8s`：启动 DSH SDK Agent，模型 `moonshotai/Kimi-K2.7-Code`，推理强度 low。
- `+6.7s`：主模型请求，0 张图。
- `+12.7s`：调用 `cad_inspect_references`；随后来源图观察请求 1 张图。
- `+75.1s`：Agent 报告已独立读取原图，观察未核验，可按相同 ROI 复看；state 中 inventory 为 SHA 匹配、`observation_valid=true`、4 条可见观察。右前竖面报 4 孔，与原图可见 3 孔不符。该视觉 finding 未人工反馈给 Agent。
- `+129.0s`：`cad_get_state`，revision 仍 0。
- `+181.5s`：创建 `main-block`，revision 1。
- `+191s` 至 `+203s`：两次 `cad_create_model` 被已有模型 / 当前 turn 未明确整体重建要求拦截；随后读取状态。
- `+209.5s`：`cad_update_feature` 无几何变化，记录相同 geometry hash 的 no-progress。
- `+215.9s`：Agent 尝试移除唯一 `main-block`；服务端返回 sole-body 保护错误，revision 保持 1。
- `+227.3s`：再次无变化更新。
- `+260s` 至 `+300s`：Agent 修改同一个 `main-block` 多次，几何 revision 2、3、4；没有添加凹槽/孔，也没有请求 preview。最后一次回退到历史 geometry signature 被 cycle guard 拒绝。
- `+300.9s`：停止前标准输出显示新模型请求仍在分析；监督端 Ctrl+C，CLI exit code 1。

## 状态边界

最终 CADPlan revision 4，仅 1 个 add feature：`main-block`，box `100×40×125 mm`，中心 `[0,0,0]`。没有凹槽、孔、预览、独立视觉复核、partial candidate 或导出文件。日志只证实 sole-body guard 阻止了主体删除；本次没有触发 duplicate-feature guard 或 preview-failure prefix fallback。
