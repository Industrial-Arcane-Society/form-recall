# F7.G4：施工阶段审查范围

审查读取宿主校验的当前 revision 里程碑。completed/expected ID 校验格式与重复；completed 必须对应当前启用特征，expected 允许尚未建立的未来特征。独立 reviewer 只收到宿主派生的 ID/kind/operation，不收到 CADPlan 参数、里程碑 notes 或预期答案。

未施工的参考图结构用 out_of_scope 单独记录，不当作当前施工缺陷。受影响 feature ID 限制为实际阶段范围。范围和完成性进入审查配对 fingerprint；旧范围结果不能直接复用。

阶段诊断可以检查当前几何的子集；但只有 expected 已完成、当前全部启用特征都在范围内、且预览非 partial 时，才有全局覆盖资格。其他情形宿主强制 covered_evidence_ids 为空，不能据此关闭全局问题。原 finding ledger 与最终验收门槛保留。

## 验证

定向集成及单测通过：双图独立请求、阶段 revision/ID 校验、重复/格式/未来 ID、out_of_scope 与实际 feature ID 过滤；两个启用特征而只审查 body 的样例可诊断但覆盖为空。不可比投影、遮挡、网格线/阴影/颜色非几何边界规则仍保留。

未运行真实视觉模型。宿主能确认 ID 与几何版本的一致性，不能仅靠 ID 确认模型对参考结构的理解正确；真实漏孔、错位、返修及新版复查由 G8 验收。G4 不据 fixture 宣称审查质量通过。
