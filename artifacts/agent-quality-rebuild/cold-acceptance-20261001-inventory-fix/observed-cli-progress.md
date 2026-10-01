# 监督端可见的 CLI 进度事件（关键摘录）

原始终端stdout/stderr未分别持久化；下列内容依据 exec-session 实时输出整理。完整结构化wire/tool事件见 `agent-observability.jsonl`，模型原始请求/响应正文不在该文件中。

- +0.2s：本地知识库无可靠文字命中；继续当前输入。
- +1.0s：启动 DSH SDK Agent，推理强度 low。
- +13.1s：首个工具 `cad_inspect_references`。
- +73.7s：CLI报告“已独立读取原图；该观察未核验，可按相同ROI复看纠正。”检查持久state确认 reference SHA匹配、3条非空 visible evidence、observation_valid=true。
- +119.7s：Agent 调用 `cad_get_state`。
- +257.1s：`cad_create_model`，revision 1。
- +265.2s、+275.6s：`cad_update_feature` 均未改变特征。
- +329.8s：`cad_add_feature center_slot`，revision 2。
- +373.4s、+382.3s：删除特征，revision 3、4；随后两次 create_model 未增加revision。
- +475.2s、+488.2s：Agent重新添加 `base_block`、`center_slot`，revision 5、6。
- +567.9s：再次添加 `center_slot`，revision 7。
- +642.0s、+656.6s、+692.6s：添加孔特征，revision 8、9、10。
- +717.8s：`cad_preview_model` 报告几何构建失败，并报告保存未验收前缀/诊断。
- +746.4s、+753.7s、+757.0s、+764.6s：特征修改/删除，最终 state 到 revision 14。
- +765.9s：监督端对 exec session 发送 Ctrl+C；退出码 1。最终核查 Python/Pythonw/Node/DSH 子进程为0。
