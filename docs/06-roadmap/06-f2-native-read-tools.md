# F2.3：Rust 原生状态、知识与联网工具

更新时间：2026-10-02。

## 迁移范围

以下 Cordis 工具通过 Rust Host 执行，运行时不调用 Python：

- `cad_get_state`：计划快照、来源、候选、视觉证据与未解决差异摘要。
- `cad_get_feature_spec`：27 类特征的参数与坐标契约。
- `cad_search_knowledge`、`cad_search_recipes`：本地工件语料、建模配方及可选原始示例图片。
- `web_search`、`web_open_result`：搜索与结果页正文读取，保存可恢复的结果 ID 和正文 SHA。

其他 CAD 写工具与视觉观察暂由兼容执行器处理，分别归入 F2.5 和 F2.4。工具注册仍使用 DSH 原生 Cordis，未引入 MCP。

## 状态与证据

会话内所有原生与兼容工具共用事务锁；原生读取和证据账本刷新使用同一状态快照，保留当前计划、版本和扩展字段。取消、损坏 JSON 和非法参数不会覆盖状态。

状态摘要每次重新核对原图文件 SHA 与稳定 ID。复核计数和覆盖必须关联当前原图与几何指纹；旧几何、旧图源、仅索引或不一致 ROI 的记录保留诊断用途，不能提升为当前证据。

知识库精确命中的几何元数据与原解析器输出来自一次性迁移快照 `src/legacy/kb/native_knowledge_enrichments.v1.json`，含 1,816 条语料 enrichment 与来源哈希。生成快照时使用了 Python；检索运行时只使用 Rust。知识语料和元数据变更会产生快照漂移提示，不能把旧模板当作完整 B-Rep。七组真实查询与 Python 的状态、排序及全部结果字段对拍一致。

联网结果允许恢复到新进程。正文哈希、结果 ID 和 URL 必须一致；搜索摘要不能代替已读取正文。过渡期间的 Python 外部事实验证器可以读取并验证 Rust 已持久化正文，避免独立 worker 丢失搜索上下文。

## 验收

- 生产应用整库 Rust 测试 55/55 通过，包含取消、损坏状态、知识命中、SHA/版本门禁、联网禁用、搜索/打开/恢复、重定向和私网拒绝。
- 新增 Rust 正文持久化的 Python 兼容验证及既有外部事实验证测试共 3/3 通过。
- 使用固定 Node `v22.23.3` 与 DSH `0.2.0-rc.2` 真实 CLI 会话，顺序完成知识检索、配方、特征契约、当前状态和网页搜索共五项调用。验收端点硬性禁止 Python 回退；会话正常退出，DSH 和 Rust stderr 均为 0。
- 实际会话 ID：`5525edfa-f35c-48fd-93e7-d2057f8b2b19`。原始轨迹和结果位于本地忽略目录 `src/apps/desktop/src-tauri/target/f2-native-read-live-01/`，未上传远程。
- 本机 DNS 将 Bing 和 DuckDuckGo 解析到 `198.18.*` 非公网地址，实际网页搜索返回 `network_error`，Agent 如实报告错误并结束。此次没有真实网页打开成功记录；搜索/正文读取成功与跨进程恢复由本地 HTTP fixture 验证。不得把本次运行写成外网搜索成功。

## 复跑

在 `src/apps/desktop/src-tauri/` 执行，预先通过环境变量设置 API key：

```powershell
cargo build --example native_read_probe --offline
.\target\f1-runtime-probe\node.exe examples/native_read_acceptance.mjs ../../../.. target/f1-runtime-probe target/f2-native-read-live-next
```

验收脚本只从环境读取密钥，把临时工具桥认证 token 传给 DSH 子进程，不把它们写入验收报告。需保留 F1 已核验的固定 runtime 目录；脚本不依赖全局 DSH，也不启动桌面 GUI。

## 尚未覆盖

这项验收没有生成模型，也没有多模态识图或 CATIA 几何验证。F2.4、F2.5、F3.3、F4–F7 继续按总 Todo 执行。
