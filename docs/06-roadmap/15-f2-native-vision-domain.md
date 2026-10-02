# F2.4.2：原生视觉工具接线

## 范围

Rust domain 接管 `cad_inspect_references`、`cad_review_reference_similarity`。DSH 使用原生 Cordis 工具桥，模型本体和循环仍由 DSH 持有；视觉请求不经过 Python worker 或 MCP。

在线配置禁用或视觉 provider 缺失时明确报错，不自动 fallback。事务锁、取消状态和原子 CAD state 保存沿用原生 executor；损坏状态不静默清空。

## 独立单视图复核

只传一张真实原图、一张当前独立视图、可信用户原文以及原图观察的证据清单。不给审查模型 CADPlan、Agent 施工记录或上一轮结论。revision、几何 fingerprint、generation、PNG SHA 及源图 SHA 必须一致。投影不可比时清空覆盖与计数，标 unobservable；高分不能放行具体 mismatch。

这只是独立视觉判断及差异记录，不代表其判断一定正确；整体完成验收另由宿主证据门禁处理。

## 验收

- domain 8 项 HTTP fixture 测试和 executor 接线测试通过；测试覆盖图片字节、可信来源绑定、旧预览、错误 SHA、高分错项及禁止 Python fallback。
- 集成状态下 Rust library 117 项测试通过（包含其他尚在接线模块，并非全部已发布）。
- 真实 DSH 0.2.0-rc.2 / Node v22.23.3 会话 `2067abce-c81a-4c01-9e3a-00fc63a59c70` 实际调用原生 Cordis `cad_inspect_references`。
- 源图 72,309 bytes；原图 SHA 与传输 SHA 一致：`53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。
- Responses HTTP 200；input 478 / output 773 / reasoning 0 / total 1251。兼容执行器为 RejectCompatibility，Python fallback 未触发。
- 真实轨迹在忽略目录 `target/f2-native-vision-dsh-live-02/`。本验收明确没有评定观察质量，不据此声称图片复刻通过。

```powershell
# src/apps/desktop/src-tauri/，密钥由环境传入，勿写入命令或文档
cargo build --example native_read_probe
node examples/native_vision_acceptance.mjs <repo> <pinned-runtime> <output-dir> <original-image>
```

当前单视图视觉复核的真实端到端质量验收仍待有实际原生模型后开展；已有真实调用只证明观察工具的注册和原图传输。
