# F2.4.1：Rust 多模态请求与图片输入基础

## 范围

完成 Rust provider 和图片输入层，尚未替换生产中的 Python 图片观察/复核 domain。F2.4 整体仍在进行。

- `native_vision_provider.rs` 支持 Chat Completions 与 Responses 两种请求形态，规范化完整 endpoint；发送真实图片字节和 high detail，校验 transport SHA。
- 请求遥测区分原图 SHA、实际发送 SHA、reference ID、ROI 和预览版本；不保存密钥、base64、完整请求体或模型推理正文。
- 使用异步 HTTP，等待响应头或正文期间都可取消；连接与请求超时有界，响应大小有上限。
- `native_vision_assets.rs` 从不可变会话 manifest 读取原图，验证 SHA、ID/index 和 MIME。整图保持原始文件字节；ROI 在内存裁剪，按 EXIF 纠正后的显示坐标解释，不产生累积裁剪文件。
- 推理预算为 provider 可表达的请求设置；不能把 provider 的软预算宣称为硬限制。

## 实际验收与缺陷

使用用户原图 `codex-clipboard-c8107ca1-5b7e-4378-9db4-efd0bbe9b9a2.png`，原文件和发送字节 SHA-256 均为 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，72,309 字节。独立 CLI 请求没有 CADPlan、已有尺寸或模型历史输入。

真实 DeepSeek Responses 返回 `status=completed`、`error=null`。首次测试暴露解析器把存在但为 null 的 error 字段误判为失败，已修复并加入 HTTP 回归样例。`incomplete/max_output_tokens` 与其他未完成原因分别返回，不能混称识图失败。

成功请求 ID：`native-67156-1790919854227-1`；模型 `deepseek-flash`，推理关闭，input=353、output=773、reasoning=0。日志位于忽略目录 `src/apps/desktop/src-tauri/target/f2-native-vision-live-03/`，未上传。

模型识别到左、中、右各三个开口，但误判凹槽底面与两侧面的关系，也未区分大小孔。**图片传输通过，观察质量未通过**。本例不代表 Agent 成功复刻，更不代表 CATIA 验收。

provider 12 项测试、图片资产 3 项测试通过，覆盖双协议、脱敏、真实 error-null 响应、取消、截断、SHA 冲突、ROI 与 EXIF。接入其他进行中模块后整库 75/75 通过；曾再次触发既有工具桥取消测试间歇失败，单项与整库复跑通过，未将其记为已修复。

## CLI

在 `src/apps/desktop/src-tauri/` 执行：

```powershell
cargo test --lib native_vision_ --offline
cargo build --example native_vision_probe --offline
```

probe 接收原图路径和输出目录，使用环境变量中的 API 密钥，输出作用域明确为图片传输与观察。生产观察/复核接线、证据生命周期和差异关闭仍属于 F2.4 后续工作。
