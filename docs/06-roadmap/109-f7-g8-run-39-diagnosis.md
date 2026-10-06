# Run39：冷启动视觉复核产物与真实 Agent 结果

日期：2026-10-06

## 运行范围

- 在 `main` 的 `9f23b1b` 基线上，使用未提交的 F7.G1.2 runner 改动进行一次真实 CLI 冷启动；该版本只增加当前 ISO 预览的安全留存和摘要状态，不改变 Agent prompt、模型配置或验收门槛。
- 使用固定 DSH `0.2.0-rc.2`、Node `v22.23.3`、当前桌面配置的 `moonshotai/Kimi-K2.7-Code`（`low`），以及默认原始参考图。原图 SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`，73,209 bytes；未注入 CADPlan、尺寸或孔数。
- 未启动可见桌面窗口或 CATIA。Run39 输出位于本机 ignored `src/apps/desktop/src-tauri/target/f7-run39-main-9f23b1b-20261006-visual/`；原始会话 scratch 已由 runner 清理。

## 安全运行摘要

- runner exit code `1`，总耗时 `2,807,299 ms`（约 46.8 分钟）；`status=failed`，scope 为 `single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance`。
- DSH 诊断为 `session_rpc` / `session/prompt` / `rpc_error`；safe projection 没有保留可进一步归因的 RPC 正文。
- 共 175 个有效 session updates、43 个 thought chunks、0 个普通 message chunks、66 个工具调用（64 completed、2 failed）、32 次 geometry revision advances。工具失败的安全终态分别涉及一次 `cad_remove_feature` 和一次 `cad_review_reference_similarity`。
- 13 个 strict gates 中 6 通过、7 失败，汇总 39 个失败原因。原图传输、主图 provider wire、DSH 进程与产物检查通过；DSH session、Cordis/ACP 工具结果、当前几何绑定、孔证据、视觉复核和 finish 未通过。该运行不是模型验收通过。

## RPC 失败的证据边界

- `dsh_process` 与子进程退出检查通过；本次有证据表明 `session/prompt` 返回 RPC error，但没有证据表明 DSH 子进程崩溃。
- runner 在 `native_agent_coldstart_acceptance.mjs` 调用 `session/prompt` 时捕获错误，只保留固定的 `stage`、`method`、`error_class` 和安全进度摘要。`native_agent_coldstart_prompt.mjs` 将 JSON-RPC `error` 统一归类为 `rpc_error`，不保留 code、message 或 data。
- 两次工具执行失败分别是 `cad_remove_feature` 与 `cad_review_reference_similarity` 的 native audit rejection；safe projection 无法证明这两次失败导致了 `session/prompt` RPC error。
- 当前不能判定 RPC 错误来自 DSH 校验、工具层、provider 或其它内部环节；也不能把 0 个普通 message chunks 推断为 provider 未工作。
- 后续诊断只增加隐私安全的标准 JSON-RPC 数字 code 分类桶和本地请求序号关联；不得记录错误正文、data、prompt、provider 内容或工具参数。对应 Todo：F7.G11。

## 提示词和几何验收约束审计

- 当前系统提示词已经要求按可比视图估算主体比例，并在主体完成后对照原图调整；也要求对 review mismatch/partial 定位受影响特征并返修。因此，不能把 Run39 的结果简单归因于“提示词完全没要求看比例”。
- 可复现的系统缺口是比例要求目前主要是自然语言。CADPlan/schema 能验证字段与几何可执行性，但不会验证 `image_estimate` 的证据是否对应原图像素比例；finish host gate 也没有单独的主体包络宽厚高比值容差门槛。
- 这说明系统允许“结构合法且能生成几何，但主体比例不符”的候选继续建模；它是值得增加硬验收的设计缺口，但仅凭当前 safe output 不能证明它是 Run39 的唯一直接根因。
- 后续 Todo：F7.G12 在主体预览后记录分视图可观测的包络比例与不确定区间，将比例 mismatch 纳入 finding 和 finish 门槛；不可比较的维度保留 unknown，不伪造尺寸。

## 视觉复核产物

新 runner 在删除 scratch 前仅发布 `visual-review/manifest.json`、`reference.png`、`model-iso.png` 三个文件。摘要记录 `visual_review_artifact_status={status:"published",code:"published"}`。Manifest 绑定 candidate revision `39`、preview generation `40` 和当前几何 fingerprint。

- 参考图：494×544，72,309 bytes，SHA-256 `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`。
- ISO 预览：768×768，40,222 bytes，SHA-256 `7bbf498ebca49ebe4cdaf8f3f2988743f5a54f4bd464a9d6533afaf9e285a8bc`。
- 独立 reviewer 只查看这两张 PNG。确认参考图为两侧宽翼板、中间窄竖向凹入及左/中/右三列各三排孔；Run39 候选是中段占比很大的深 U 形腔体。候选可见孔位约为左侧三孔、右侧四孔和凹腔后壁一孔，未复现 3×3；右列孔径序列也不符合大—小—大。由于 ISO 遮挡，不能确认未显示面的孔位；未估算尺寸。

因此，F7.G1.2 的安全图像导出契约在一次真实运行中得到验证，但本轮实际模型与参考图明显不符，视觉 gate 仍失败。导出预览能证明模型生成了 PNG，不能证明几何正确或 finish 通过。

## 独立的安装版 GLB 显示问题

用户截图里的旧错误是 Rust 拒绝从预览缓存目录之外读取 app-data 下的 `.glb`。已安装 0.1.1 含有 `e2496f6` 路径修复；当前源码的 `asset_path_policy` 回归为 2/2，覆盖允许 app-data GLB 和拒绝 junction 越界。Run39 是 CLI 图片产物，不是已安装桌面窗口验收；因此这次运行不替代安装后可见视口验证。
