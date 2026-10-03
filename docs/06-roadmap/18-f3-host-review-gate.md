# F3.3.2 宿主整体复核与完成门禁

整体复核绑定当前 revision、几何指纹、宿主几何检查、孔测量、原图覆盖和未关闭差异。完成前重新计算证据摘要并核验宿主签发记录。Agent 自填分数和单独 `match` 字段不能替代验收。

仅采样射线通过、没有实际孔径测量时返回 `partial`，不能 `accept`。当前候选未通过复核仍可保存并明确标记未验收；旧版本、缺失或篡改记录不能完成。

用户给定的参数逐项绑定引用片段与原始 turn，坐标需要引用全部数值。数量和角度不换算为长度，停用孔不计入当前孔核验。

## 验证记录

2026-10-02：session 定向测试 18/18、review gate 7/7；整体 crate 132/132 通过。命令在 `src/apps/desktop/src-tauri/`：

```powershell
cargo test --lib native_cad_session
cargo test --lib native_review_gate
cargo test --lib
```

这些测试包含受控证据夹具，证明门禁语义，不代表真实多模态审查质量通过。真实 DSH 建模与盲测属于后续 F7。
