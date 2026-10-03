# F7.G4：施工阶段审查范围

审查读取当前 revision 的施工里程碑，但里程碑只是 Agent 可写的范围声明，不能单独证明施工成功。进入阶段范围前，宿主现在要求 `last_preview` 对应当前 revision 与 CADPlan fingerprint，几何报告状态为 passed，并校验 `native_cad_session.complete_preview` 写入的报告 SHA attestation。completed ID 还须唯一、格式有效并对应当前启用特征；expected 可包含未来 ID。独立 reviewer 只收到宿主派生的 ID/kind/operation，不收到 CADPlan 参数、里程碑 notes 或预期答案。

未施工的参考图结构用 out_of_scope 单独记录，不当作当前施工缺陷。受影响 feature ID 限制为实际阶段范围。范围和完成性进入审查配对 fingerprint；旧范围结果不能直接复用。

阶段诊断可以检查当前成功构建几何的子集；只有 expected 已完成、当前全部启用特征都在范围内、且预览非 partial 时，才有全局覆盖资格。没有与当前几何绑定的宿主签署成功构建报告时，复核在 provider 调用前拒绝。其他不完整阶段仍强制 covered_evidence_ids 为空，不能据此关闭全局问题。原 finding ledger 与最终验收门槛保留。

## 验证

定向测试 `native_vision_domain::tests::` 19/19、`native_review_findings::tests::` 5/5、`native_executor_inspect_current_review_and_get_state_preserve_current_bindings` 1/1 通过；随后 Rust 单线程全量 296/296 通过。executor 的 current review/get-state 集成 fixture 也改为通过 `native_cad_session::complete_preview` 写入匹配当前 revision/fingerprint 的 OCCT 报告 attestation，使它继续检验生产 review/summary bindings，不绕过新的 scope 门禁。enabled hole 虽被 milestone 自报 completed、但缺少当前成功构建 attestation 时仍在 provider 前拒绝；mocked provider 响应经过生产 normalize→reconcile 后只为归属当前 completed feature 的 mismatch 创建 finding。原有阶段 revision/ID、重复/格式/未来 ID、out_of_scope 与实际 feature ID 过滤和移除特征 finding 生命周期回归仍通过。

未运行真实视觉模型。宿主能确认 ID 与几何版本的一致性，不能仅靠 ID 确认模型对参考结构的理解正确；真实漏孔、错位、返修及新版复查由 G8 验收。G4 不据 fixture 宣称审查质量通过。
