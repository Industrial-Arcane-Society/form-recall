# F7.G8.1：CLI 共用生产 Agent profile

新增 `native_agent_profile` Rust example，通过 stdin 接收与桌面相同的 provider JSON，调用同一个 `Provider::parse`、路由识别与 profile patch 生成器。返回安全摘要：实际路由、模型、请求推理强度、可配置性、输出/上下文限制及生产提示词路径和 SHA256；不输出密钥。另提供同源 ACP 推理配置入口。

```powershell
cargo test --manifest-path src/apps/desktop/src-tauri/Cargo.toml --example native_agent_profile --offline
cargo build --manifest-path src/apps/desktop/src-tauri/Cargo.toml --example native_agent_profile --offline
```

定向测试 1/1，验证自定义上下文 1048576、输出 12000 与请求 high 原样参与生产配置，patch和安全摘要均无测试密钥。没有启动窗口、DSH或调用模型。

这是替换硬编码验收脚本的基础，尚非建模验收 runner：执行、工具轨迹、新鲜输出、多轮/取消和真实图片证据仍未完成。原未跟踪冷启动脚本保持不作为通过证据。`reasoning_effort_requested` 不冒充 provider 实际生效值。

## 后续：生产配置图片 runner

`native_agent_coldstart_acceptance.mjs` 已替换硬编码档案与只读提示词。它读取桌面当前 provider、调用 Rust 生产 profile 与推理选项选择器、加载生产 system.md、注册原生工具并提供 OCCT runtime。要求全新输出目录，原图不可变校验；不注入尺寸或特征答案。运行时限默认 1800 秒，可由 `AIMODELER_ACCEPTANCE_DEADLINE_SECONDS` 配置。错误与输出分开归档，ACP/传输完成不等于模型验收通过。

Rust profile/推理选项测试 2/2，Node 语法检查通过。2026-10-03 实际启动当前桌面选中的 SiliconFlow `moonshotai/Kimi-K2.7-Code` / low 新会话：第一个 provider wire 请求包含原始 72309 字节 PNG，source与transport SHA256 均为 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`；已接收真实 reasoning stream。证据目录 `temp/acceptance/f7-production-image-20261003-run01/`。此时会话仍运行，建模结果和审查质量尚未结论；本记录不把请求成功算为 F7 通过。

```powershell
node src/apps/desktop/src-tauri/examples/native_agent_coldstart_acceptance.mjs . src/apps/desktop/src-tauri/target/f1-runtime-probe temp/acceptance/FRESH_RUN PROVIDER_CONFIG_JSON
```

参数里的配置文件可以是桌面完整配置，或等价的单个 provider 对象。密钥不进入 patch、命令行或安全配置摘要。原来的字符串 selfcheck 已移除。
