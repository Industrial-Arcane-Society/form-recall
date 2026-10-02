# F3.3.1：原生 CAD 会话事务与验收门禁

## 范围

纯 Rust 会话 domain 提供增量建立、增加、修改、删除、恢复候选、请求预览、提交后端预览、验收及提交导出。每次操作克隆状态；错误不改变输入。实际几何和工具 dispatch 属于后续接线，本项不代表完整 Agent 运行通过。

## 状态与证据

- 真实几何变化推进 revision 并使旧预览失效；数值 no-op 及单独来源更新不伪造几何版本。
- 其他特征和来源保留。候选恢复产生新版本，旧候选仍可追溯。
- preview/build 后端结果必须绑定当前 revision 和 geometry fingerprint；部分诊断预览不能冒充完整可恢复模型。
- 请求孔数、自评分及 requested 状态不能冒充独立孔测量。
- 图片验收需要整体 geometry_review，且其 revision/fingerprint 匹配当前模型。逐图覆盖率不能代替整体判定。
- 用户来源绑定宿主记录的真实 turn 和逐字引文；临时 turn_id 进入宿主 action 审计而不保留在 CADPlan source 中；虚构 turn、自报 verified、缺引文或数值不支持均拒绝。

用户来源的标量检查目前是字面数值/单位与参数词线索，不是完整自然语言语义解析；向量、布尔、枚举仅作引文/turn 绑定。复杂文本仍需继续改善。未把这一限制称为完整来源证明。

## 验收

```powershell
# src/apps/desktop/src-tauri/
cargo test native_cad_session::tests --lib
```

16 项定向测试通过：失败回滚、no-op、来源保留、预览失效、候选恢复、旧后端结果、部分预览、来源正负例、旧整体复核及只有高覆盖率却缺整体复核等。

当前使用合成状态与可信 manifest fixture 验证门禁；尚未通过真实 DSH -> 写工具 -> 几何 -> 视觉 -> 完成的全链路。CATIA 未执行。
