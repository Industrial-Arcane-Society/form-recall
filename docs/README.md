# 研究与工程文档

此页是 `docs/` 唯一的根级 Markdown 索引。目录采用两位数字前缀；文档记录研究问题、实验方法、结果与局限，同时提供实现和复现入口。

本页列出常用入口与阶段验收精选文档；完整 F0–F7 状态以迁移 Todo 为准，其他阶段卡与历史运行证据可从 `06-roadmap/` 查找。

## 从这里开始

- [研究目标、问题与证据规则](00-research/01-research-charter.md)
- [当前系统审计与迁移前基线](00-research/02-current-system-audit.md)
- [目标架构与模块边界](01-architecture/01-target-architecture.md)
- [实验和验收方法](02-methods/01-validation-protocol.md)
- [实验记录模板](03-experiments/01-experiment-record-template.md)
- [桌面端界面设计规范](04-design/01-frontend-design-system.md)
- [构建、启动和验证](05-operations/01-build-and-run.md)
- [浏览器预览启动说明](05-operations/02-browser-preview-startup.md)
- [Tauri 开发命令与启动预检](05-operations/03-tauri-dev-cli-preflight.md)
- [完整迁移 Todo 与提交状态](06-roadmap/01-migration-todo.md)
- [F1 Rust ACP Host 与固定 Runtime](06-roadmap/02-f1-acp-host.md)
- [F2.1 原生 Cordis → Rust 本机工具桥](06-roadmap/04-f2-cordis-bridge.md)
- [F2.2 Rust 持有 Cordis 工具 schema](06-roadmap/05-f2-rust-tool-schemas.md)
- [F2.3 Rust 原生状态、知识与联网工具](06-roadmap/06-f2-native-read-tools.md)
- [F2.5 Rust 原生 CAD 写工具](06-roadmap/21-f2-native-cad-write-tools.md)
- [F3.1 Rust CADPlan 增量事务](06-roadmap/07-f3-plan-transactions.md)
- [F3.2 Rust CADPlan Schema 与静态校验](06-roadmap/08-f3-plan-validation.md)
- [F4.2.4 CADPlan pattern 原生执行](06-roadmap/22-f4-native-pattern-execution.md)
- [F4.2.5 CADPlan piston 原生执行](06-roadmap/23-f4-native-piston-execution.md)
- [F4.2.14 CADPlan V 带轮原生执行](06-roadmap/63-f4-native-vbelt-pulley.md)
- [F4.2.20 CADPlan impeller 原生执行](06-roadmap/68-f4-native-impeller.md)
- [F4.2.21 CADPlan shell 原生执行阻塞记录](06-roadmap/69-f4-native-shell-blocker.md)
- [F6.3 Rust 原生零件目录与示例列表](06-roadmap/55-f6.3-native-parts-catalog.md)
- [文档目录、索引与链接审查记录](06-roadmap/03-docs-index-audit.md)

## 归档说明

旧版说明、阶段计划和运行证据原样保存在 [`../temp/`](../temp/)；归档内容不自动代表当前实现，也不作为通过当前验收的证明。需要引用历史结果时，应回到其输入、运行记录和产物核对。
