# F7.G2：生产 Quick 几何到隐藏 WebView 网格验收

日期：2026-10-06  
范围：P0，验证真实生产几何构建产物能经活动模型事件到达生产 Vue 视口。

## 改动

`ai-modeler-webview-smoke` 的 `production-quick-preview` 场景直接调用与生产 `quick_build` 共用的 `build_quick_active_model` 路径（经 `headless_quick_smoke` 入口）。固定提示词创建明确尺寸的长方体；生产解析器生成 CADPlan，原生 Rust/C ABI OCCT 建实体并网格化，生产 Quick exporter 写 GLB/AIMODEL/STL/OBJ，生产 active-model transaction 提交当前模型。smoke-only Agent channel 再把已提交文档发成 `model.changed`。生产 Vue 收到模型路径后调用生产 `read_asset` command，经 `GLTFLoader.parse`、视口 mesh 替换与 fit-view；runner 等待 viewport loaded 状态及有效 WebGL canvas。

旧的手工三角面 GLB builder 已移除。该场景要求存在 OCCT 7.9.3 runtime manifest；缺失时 runner 以清楚的前置条件错误退出，不回退到合成 GLB。其他纯问答、候选失败和验收状态场景仍使用隔离事件 fixture。

## 验收记录

- `npm.cmd ci --offline --no-audit --no-fund`：通过，离线安装 61 个依赖包。
- `npm.cmd run build`：通过；Vite 仅报告既有 `CadViewport` chunk 637.32 kB、gzip 160.29 kB 的非阻断 warning。
- `node --check scripts/test-agent-webview.mjs`：通过。
- `rustfmt src-tauri/src/webview_smoke.rs --edition 2024`：通过并格式化本次 Rust 文件；全 crate `cargo fmt --check` 会命中仓库既有大量未格式化 examples，未据此改写其他文件。
- smoke feature 单测：设 `$env:TAURI_CONFIG='{"bundle":{"resources":[]}}'`、`$env:AIMODELER_OCCT_MANIFEST=<OCCT 7.9.3 runtime-manifest.json>`、`$env:AIMODELER_TEST_OCCT_MANIFEST=$env:AIMODELER_OCCT_MANIFEST`，运行 `cargo test --manifest-path src-tauri/Cargo.toml --lib --features agent-webview-smoke,tauri/custom-protocol --offline --no-run` 编译；为生成的 test exe 临时附加 Common Controls v6 `.manifest` 并运行 `webview_smoke::tests --nocapture`：7 passed / 0 failed。其中 headless production Quick 集成测试测得 `[100, 60, 20] mm`、单个闭合实体、GLB `glTF` header、输出路径写入 active model 且 generation 为 1。仓库 `test-lib.ps1` 默认不启用 `agent-webview-smoke`，单独使用 `-Filter webview_smoke` 会得到 0 tests，不能代替此 feature 测试。
- smoke binary：在同一 `TAURI_CONFIG` 覆盖下执行 `cargo build --manifest-path src-tauri/Cargo.toml --bin ai-modeler-webview-smoke --features agent-webview-smoke,tauri/custom-protocol --offline`，通过。
- `npm.cmd run test:agent-webview`：在 Windows WebView2 `154.0.4258.53`、同版本 EdgeDriver、`tauri-driver` 和本地 OCCT 7.9.3 manifest 下退出码 0。生产场景与其后四个状态 fixture 全部 PASS。Drivers 通过另一个 worktree 的既有工具目录只读调用；该目录未修改。
- 初次真实 WebView 运行让四个 fixture 的 revision `17–19` 先更新 Vue，再投递 active model 的真实 generation `1`，被生产 Vue 的 revision guard 正确忽略，因而出现 WebGL 等待超时。将生产 Quick 场景排在 synthetic revision fixture 之前后复跑成功；runner 同时加入了 production 错误早停和超时末态摘要，便于识别 OCCT/build/asset 错误。
- `git diff --check`：通过。

## 边界

该用例可证明生产 Quick→OCCT→活动模型文档/GLB→Channel `model.changed`→生产 IPC `read_asset`→Three.js `GLTFLoader`→隐藏 WebView canvas 路径。它不证明实际 DSH/provider 请求、可见界面像素质量、MSI 安装后的状态或 CATIA 互操作。若环境缺 OCCT DLL、WebDriver、WebView2 或前端依赖，分别作为环境阻塞报告，不将旧 GLB fixture 的结果记作生产几何通过。

## 2026-10-06：Agent workspace GLB 路径复核

- 为直接覆盖 Agent 的实际产物目录层级，隐藏 WebView 首个生产场景现在将 OCCT 实建生成的 GLB 复制到该隔离 smoke app 的 `agent-host/workspaces/session-f7-webview-smoke/native-output/generation-f7-webview-smoke/candidate.glb`，并把此路径放进 `model.changed` 文档；生产 `read_asset` 必须读取它，Vue/GLTFLoader 必须完成加载并留下可用 WebGL canvas。没有调用真实 DSH/provider；几何由生产 Quick→OCCT smoke 生成，路径模拟 Agent workspace。
- 将这个生产场景排在四个较高合成 revision 状态 fixture 前，以免真实 generation 被生产 Vue 的 stale-revision guard 忽略。隐藏 WebView 共 5 个场景全部通过：Agent 深层 GLB 路径 1 个，纯问答、工具失败、accepted-current、stale-finish 4 个。
- 第一次运行因 Rust smoke 固定回复仍为旧 Quick 文案、runner 已改等 Agent workspace 文案，造成期待文本不一致的 timeout；检查时模型事件已经到达且 canvas status 为“预览已更新”。统一 fixture 回复后重新构建 smoke binary，完整复跑 exit 0。测试删除本次复制的 `candidate.glb` 文件；不清理其上层目录或用户数据。
- 这比单元测试白名单更进一步，但仍是隔离隐藏 WebView；不证明 MSI/NSIS 安装版或用户原始 Agent 工件的可见窗口结果。完整过程见[F6.10 GLB 安装与路径报告](./F6.10-installed-glb-preview.md)。

## 2026-10-06：活动模型冷启动恢复到 WebGL

- 在 `origin/main` 基线 `f74f7b9` 上，smoke binary 新增第二个独立隐藏 WebView session。setup 在 smoke app-local-data 中经生产 Quick→OCCT builder 提交原生活动模型记录及 GLB；Vue startup 请求的 snapshot 共用 production `active_model_snapshot_value`（与正式 `active_model_snapshot` command 相同），随后生产 `read_asset`、`GLTFLoader`、`CadViewport` 处理已恢复的 mesh。没有用 `model.changed` 事件触发该次加载，也没有使用合成 GLB、真实 DSH、provider 或 CATIA。
- `npm.cmd run test:agent-webview` 在 WebView2/EdgeDriver `154.0.4258.53`、`tauri-driver 2.1.0` 和 OCCT 7.9.3 manifest 下 **6/6 场景通过**：原有生产 Agent-workspace preview、四个 Channel 状态 fixture，以及新的 cold-start active-model→read_asset→GLTFLoader→WebGL。窗口 `visible:false` 且不在任务栏；CLI 收尾回收 driver/WebView 进程。
- 恢复 mesh 的 smoke GLB 位于 smoke app-local-data；生产 path policy 允许该根下的 `.glb`。因此此项证明受支持 app-local-data 活动模型恢复可将 mesh 加载到隐藏 WebView viewport；它不验证旧截图中的那份 5-feature GLB，也不证明当前已安装 0.1.2 的用户可见窗口。
- 用户截图文件时间 `2026-10-05 10:55:05 +08:00`，其中的 `asset_outside_allowed_roots` 发生于 10:50–10:52，早于 `e2496f6`（11:28）增加 app-local-data `.glb` 读取分支。当前 smoke 复用的活动模型恢复快照会走相同 production `read_asset` path gate；这次允许根内路径通过。旧截图不能作为当前安装版本对该资产的复现；需用用户文件当前持久化 `mesh_path` 的实际根路径和当前运行日志另行确认是否落在白名单外。
- 修复一个启动状态覆盖竞态：存在恢复 `mesh_path` 时，`initialize()` 不再在末尾把 viewport 的快速“预览已更新”或读取失败状态覆写成“原生 CAD 工作区已连接”。该状态只由 viewport 的 load/error 回调报告，回归中检查加载成功状态与有效 WebGL canvas。

## 2026-10-06：活动模型冷启动恢复到 WebGL 的 CLI 覆盖核对

- 本轮在最新 `origin/main` (`575652f`) 上离线安装前端依赖后，活动模型恢复 helper **3/3**、Rust `asset_path_policy` **2/2**、`test:agent-channel-vue` 的四个 MockRuntime→Vue Channel 场景和三个 production-App startup hydration 场景（**7/7**），以及 `test-agent-webview.mjs` 的 Node 语法检查均通过。Channel/Vue 测试边界仍不包含 WebView delivery。
- 检查发现 smoke Host 的 `active_model_snapshot()` 固定返回 `null`。5 场景 WebView harness 的几何场景会在页面已启动后生成 Quick 模型并发布 `model.changed`，测试 `read_asset → GLTFLoader → WebGL`；生产冷启动恢复路径没有进入该场景。单独的 active-model-restore hydration 测试使用模拟 IPC，不检查网格绘制。
- 本轮 `test:agent-webview` 受环境阻塞，在启动前报告缺少 `tauri-driver` 和与当前 WebView2 Runtime `154.0.4258.53` 匹配的 `msedgedriver`。隔离工作树也没有 OCCT runtime manifest；若补齐 drivers，需再设置 `AIMODELER_OCCT_MANIFEST` 到已验证 OCCT 7.9.3 runtime。没有启动可见 GUI、完整打包/安装版或 provider。
- 因此先前的 5/5 仍只证明新建 Quick/Agent-workspace 模型事件经过生产读取与绘制链路；它不证明 active-model 冷启动恢复后显示，也不证明 MSI/NSIS 安装版。当前未找到生产缺陷，本轮结果是覆盖缺口与精确前置条件记录。
