# 原生桌面迁移验证记录

记录日期为 2026-11-20，迁移分支为 `feat/mygo-native-workbench`，比较基线为 `cf69659`。验证对象包括独立 Rust 核心、常驻 JSON Lines 宿主、MyGo 工作台、原生视口和 Tauri 兼容入口。研究功能按现有实现保留，本轮范围是桌面和模块构造迁移。

本记录汇总已执行的检查。首轮交付后的用户验收发现视口空白，后续已定位窗口层级覆盖问题，修复和桌面像素回归见末节。`temp/outputs` 中的证据属于本机输出，通常受 Git 忽略规则管理，克隆仓库后需按文中的命令重新生成。

## 环境与模块

| 项目 | 本次环境或位置 |
| --- | --- |
| 平台 | Windows x64，PowerShell |
| Go / UI | Go 1.27.4，MyGo v0.9.1 |
| Rust | MSVC 工具链，core 和 host 的最低 Rust 版本声明为 1.90 |
| 几何 | 通过 runtime manifest 校验的 OCCT 7.9.3，桥接 ABI 1 |
| 核心 | `src/core/cad-engine` |
| JSON Lines 宿主 | `src/apps/engine-host` |
| MyGo 桌面 | `src/apps/native-desktop` |
| 兼容入口 | `src/apps/desktop/src-tauri` 与 Vue |
| 基线和后端证据 | `temp/outputs/mygo-backend` |

默认构建、测试与打包脚本的参数和缓存位置见构建指南。[原生桌面构建](03-native-desktop-build.md)

## 已完成检查

| 检查 | 结果 | 证据与范围 |
| --- | --- | --- |
| 独立 core 和 host 构建 | 通过 | `temp/outputs/mygo-backend/host-build.log` |
| Tauri all-targets 与 WebView smoke feature 编译 | 通过 | `temp/outputs/mygo-backend/tauri-check.log`，覆盖编译入口 |
| Host 进程协议回归 | 3 项通过 | `temp/outputs/mygo-backend/host-tests.log`，含真实 OCCT 与隔离 ACP 夹具 |
| Tauri IPC 场景 | 4 种通过 | `temp/outputs/mygo-backend/tauri-ipc-*.log`，过时 finish、纯问答、工具失败、当前版本验收 |
| 旧 Agent Channel → Vue 入口 | 4 种对话与 4 种恢复、案例场景通过 | `test-agent-channel-vue.ps1` 的本次终端结果，Vue 使用无界面渲染器 |
| 迁移路径及适配断言的 Node 检查 | 20 项通过，1 项跳过，0 项失败 | 6 个定向测试文件，跳过项需要本地 `@deepseek-ai/dsh-tools` |
| 原生诊断与启动检查 | 9 项通过 | `test-native-diagnostics.ps1` 的本次终端结果 |
| 原生在线查询检查 | 4 项通过 | `test-native-online-lookup.ps1` 的本次终端结果 |
| Python 在线查询一致性 | 5 项通过 | `test_native_online_lookup_parity.py` |
| 零件目录 Rust/Python 一致性 | 通过 | 66 个零件、17 个样例、4 类查询 |
| Go 静态检查 | `go vet -mod=readonly ./...` 通过 | 文档整理时重新运行，包含新增跨进程测试 |
| 最终 Go 原生桌面测试 | 59 项顶层测试通过，4 项按环境条件跳过 | `temp/outputs/native-desktop/final-go-tests.jsonl`，包含引擎、视口、工作台和入口 |
| 真实 release host → 工作台回归 | 通过，1.03 秒 | `TestHostWorkbenchIntegration`，使用重新构建的 release 宿主单独启用，详情见下一节 |

Node 跳过项是固定 DSH 包的 `defineTool` 行为检查。动态 schema 的迁移路径已由同文件另一项测试读取。诊断和在线查询脚本会检查筛选结果，匹配到零项 Rust 测试时直接报错。

源码迁移审查核对了 125 个字面量 `include_str!` 和 `include_bytes!` 引用、Cargo path dependency、构建入口与历史测试路径，并修复 12 个现用脚本或断言文件。仓库当时的 `.github` 目录仅包含 PR 模板，未发现 CI workflow。Quick 样例审计脚本本次只核对了新目录 JSON 加载与 66/17 项数量，完整样例建模审计需另行运行。

## 真实引擎与工作台回归

测试通过 `engine.Start` 启动真实 Rust 宿主，再调用 `New(client, viewport.New())` 和正常工作台初始化。MyGo UI Tester 输入需求、点击生成按钮、编辑数值与数组并点击应用。真实 OCCT 构建出的 GLB 经生产视口加载器解码，断言核对其顶点边界、三角形数量和宿主统计。[测试实现](../../src/apps/native-desktop/internal/workbench/host_integration_test.go)

| 步骤 | 实测结果 |
| --- | --- |
| Quick 输入 | `创建 100×60×20 毫米长方体` |
| 初始模型 | generation 2，12 个三角形，尺寸 100×60×20 mm，初始化先创建空工程 |
| 数值编辑 | 长度从 100 改为 120 mm，GLB 与宿主尺寸一致 |
| 数组编辑 | 中心改为 `[12,-7,5]`，GLB 最小与最大边界均按该向量平移 |
| 保存 | 工程 4969 字节，数值与数组参数保持完整 |
| 进程重启 | 恢复保存后的 generation 5、几何指纹和模型，界面处于已保存状态 |
| 新建与导入 | 新建清空视口，再导入工程，实际尺寸和边界与保存前一致 |
| STL | 684 字节，二进制长度、三角形数量与所有顶点边界匹配 GLB |
| OBJ | 1078 字节，包含顶点和面 |
| 几何报告 | 1654 字节，JSON 中 `valid` 为 true |
| 四视图 | 可完整解码的 1536×1536 PNG |
| 验收保护 | 裸文档的自报验收字段和工程中的伪造验收投影均保持候选状态，审核案例写入被核心拒绝 |

测试数据使用 `t.TempDir`，由测试清理。该回归使用无界面 UI Tester 和实际网格加载。独立窗口测试验证 GPU 渲染，桌面像素回归验证最终呈现。工程伪造夹具保留原始 JSON 数值表示，只添加验收字段，以保持 CADPlan 与特征参数的一致性。

从仓库根目录显式设置三个路径后运行。缺少 `FORMRECALL_TEST_ENGINE` 时测试跳过，已设置引擎而缺少其他必要路径时测试失败。

```powershell
$env:FORMRECALL_TEST_ENGINE = (Resolve-Path 'src/apps/engine-host/target/debug/formrecall-engine-host.exe').Path
$env:FORMRECALL_TEST_RESOURCES = (Resolve-Path 'src/legacy').Path
$env:FORMRECALL_TEST_OCCT = (Resolve-Path 'temp/build-cache/occt-runtime/runtime-manifest.json').Path
Push-Location src/apps/native-desktop
try {
    & ../../../.tools/go/bin/go.exe test -mod=readonly ./internal/workbench -run '^TestHostWorkbenchIntegration$' -count=1 -v -timeout=8m
} finally {
    Pop-Location
}
```

引擎路径应指向本次待验证的实际构建产物。最终回归使用 `temp/outputs/native-desktop/release/formrecall-engine-host.exe`。早期回归使用独立构建的 debug 宿主，最终 release 运行再次验证了同一套 Quick、编辑、恢复和导出流程。

## 原生窗口与视口

独立 MyGo 窗口测试报告时间为 `2026-11-20T17:26:20+08:00`，结果为 passed。GPU 为 NVIDIA GeForce RTX 5060 Laptop GPU，窗口 DPI 为 144。检查覆盖实际子 HWND 尺寸、窗口缩放后的布局、模型像素合成、遮罩隐藏视口、动画插值与收敛，以及减少动画偏好。[视口报告](../../temp/outputs/native-viewport-verification.json)

| 数据 | 实测值 |
| --- | --- |
| 真实网格 | 604 个三角形，1 个网格 |
| GLB 解析 | 1.087 ms |
| 真实网格绘制 p95 | 6.763 ms |
| 扩展负载 | 20×20 个平移副本，241600 个三角形、400 个部件，保留为一个 GPU 网格 |
| 扩展负载绘制 p95 | 6.922 ms |
| 空闲观察 | 2.000 秒，增加 1 帧，进程 CPU 计时增量为 0 ms |

计时使用预热 8 帧后的 120 帧旋转，包含绘制、交换、WGL 上下文切换和 `glFinish`，衡量该设备上的同步渲染耗时。桌面实际呈现帧率与多显示器之间的物理 DPI 切换仍需对应实测。空闲 CPU 的零增量受计时精度与观察时长限制。[原始报告与限制](../../temp/outputs/native-viewport-verification.json)

首轮截图与录屏使用窗口内容和 WGL 帧缓冲合成，记录了模型渲染和界面布局。用户随后报告桌面视口空白，确认这类合成检查漏检了桌面窗口层级覆盖问题。历史文件保留供追溯。[首轮录屏和截图](../../temp/outputs/native-ui-motion/final-30fps/)

## 设置界面与动画回归

设置窗口已重设为 800×600 DIP，并限制在窗口宽高减 48 DIP 的范围内。160 DIP 左侧导航包含模型服务、显示与行为、几何计算和运行环境，右侧表单独立滚动，底部保存和取消保持固定。模型服务添加、删除使用 SVG 图标和原生菜单、确认对话框，运行环境显示 OCCT、DSH 和 CATIA 的状态及必要诊断信息。

面板进入使用 160 ms 淡入和 8 DIP 位移，退出使用 120 ms 淡出和 4 DIP 位移，背景遮罩过渡为 160 ms，分类切换为 140 ms 淡入和 6 DIP 位移。系统启用 ReduceMotion 时直接切换。视口在关闭动画期间保持隐藏，工作台使用已捕获的模型画面作为背景，退出完成后恢复原生子窗口。设置 overlay 每帧保留父节点以完成退出，关闭状态允许主界面输入穿透。

| 检查 | 结果 |
| --- | --- |
| 1440×900 和 980×640、浅色和深色、四个分类的实际导航点击 | 保存、取消、关闭按钮和弹窗尺寸均在窗口内 |
| 跨分类修改模型名称、在线行为和几何计算时限 | 保存后保持修改值和未知扩展字段，取消后恢复上次保存值 |
| 980×640 展开分阶段配置并滚动到底部 | 复核模型可编辑，左侧导航和底部操作位置不变，切回模型服务时回到表单顶部 |
| 模型获取请求进行中关闭设置 | 保留编辑草稿并禁用取消，完成后可正常取消 |
| 进入、退出和减少动画偏好 | 图像逐帧比较确认正常过渡、减少动画时即时完成，关闭 180 ms 后背景恢复且无残留帧请求 |
| 删除服务与阶段覆盖 | 保留其他服务、阶段模型和未知字段，删除最后服务后保持空列表 |

设置定向测试在最终布局接入后通过，测试使用 MyGo UI Tester，包含真实点击、键盘输入、滚动和图像比较。截图矩阵用于确认布局和字重。[测试记录](../../temp/outputs/native-settings-redesign-tests.log) [设置截图](../../temp/outputs/settings-redesign/) [测试实现](../../src/apps/native-desktop/internal/workbench/settings_test.go)

首轮合成录屏为 1598×1038，共采集 517 帧，采样区间 19.077 秒，平均 27.10 帧/秒。17 个阶段均达到目标状态，其中 16 个动画阶段在操作后 450 ms 内分别捕获 5 至 8 个不同画面。操作涵盖特征选择、日志展开收起、属性栏伸缩、设置进入退出、四个设置分类和四个模型视角。按钮颜色过渡为 120 ms，属性栏宽度过渡为 220 ms，日志高度过渡为 200 ms。

录制进程启动独立的 MyGo 窗口，通过 PrintWindow 读取窗口内容，并在子窗口实际位置合成其 WGL 帧缓冲。操作调用生产控件使用的工作台回调和状态转换。原始像素直接交给 FFmpeg，时间戳随实际采样时间推进。该采样率只描述录屏采集速度，显示器呈现性能仍需单独测量。录屏环境使用 NVIDIA GeForce RTX 5060 Laptop GPU，模型包含 604 个三角形，桌面鼠标与其他窗口未参与采集。[动画视频](../../temp/outputs/native-ui-motion/final-30fps/native-ui-motion.mp4) [逐帧记录](../../temp/outputs/native-ui-motion/final-30fps/recording.json) [测试日志](../../temp/outputs/native-ui-motion/high-rate-test.log)

从仓库根目录设置真实宿主、资源、OCCT 和 FFmpeg 路径后可复现。该测试仅在显式启用时创建窗口和录屏。

```powershell
$env:FORMRECALL_UI_MOTION = '1'
$env:FORMRECALL_TEST_ENGINE = (Resolve-Path 'temp/outputs/native-desktop/release/formrecall-engine-host.exe').Path
$env:FORMRECALL_TEST_RESOURCES = (Resolve-Path 'src/legacy').Path
$env:FORMRECALL_TEST_OCCT = (Resolve-Path 'temp/build-cache/occt-runtime/runtime-manifest.json').Path
$env:FORMRECALL_TEST_FFMPEG = (Get-Command ffmpeg.exe).Source
$env:FORMRECALL_UI_MOTION_OUTPUT = Join-Path (Get-Location) 'temp/outputs/native-ui-motion/replay'
& ./.tools/go/bin/go.exe -C src/apps/native-desktop test -mod=readonly ./internal/workbench -run '^TestNativeWorkbenchMotionRecording$' -count=1 -v -timeout=3m
```

## JSON Lines 取消与配置路径回归

Go 客户端的写入队列支持在等待写入权时取消，已进入 Windows 同步管道的写入取消会终止失效传输并回收子进程。关闭过程先终止子进程，再关闭输入管道，避免等待被阻塞写入持有的管道锁。三个子进程回归分别验证阻塞写入在 100 ms 请求超时后返回并回收、等待写入的请求取消后当前传输仍可继续，以及无请求超时的阻塞写入能通过 `Close` 结束。本次实测分别为 0.13、0.55 和 4.04 秒。[取消测试记录](../../temp/outputs/native-engine-cancellation-tests.log) [测试实现](../../src/apps/native-desktop/internal/engine/client_test.go)

Rust 宿主显式接收 `--resources` 和 `--data` 时，配置路径按 `--config`、`AIMODELER_CONFIG_PATH`、`AIMODELER_DATA_DIR/config.json`、`--data/config.json` 的顺序解析。独立 Release 进程的 handshake 回归覆盖四种组合并通过，保留已有用户配置环境变量的兼容性。[配置路径测试记录](../../temp/outputs/native-host-config-path-tests.log) [测试实现](../../src/apps/engine-host/tests/config_paths.rs)

配置修复后的阶段性 Rust release 构建使用 `--locked --offline --release`，宿主为 21643776 字节，SHA-256 为 `36fd798f01d7d2c5d83d6d4c457d3e2ee649866fbc684c3f87fb05c18b8d5291`。该宿主用于前述跨进程回归与动画录屏。源码提交后重新构建的交付包见末节。构建仍有既有的 `standard_profile` 未使用警告。[构建记录](../../temp/outputs/native-rust-release-final.log)

## Rust 全量基线与 23 项失败

迁移前 `cf69659` 被独立提取并在同机运行。基线为 483 通过、23 失败、63 忽略，独立核心为 482 通过、23 失败、63 忽略。失败名称逐项一致，新增失败列表为空。通过数相差一项对应留在 Tauri 适配 crate 的 IPC 测试，该测试另外运行了四种场景。[比较汇总](../../temp/outputs/mygo-backend/verification-summary.json)

全量核心测试仍返回失败。以下按日志中首次失败断言归类，完整根因修复和输入影响范围需继续分析。

| 数量 | 日志中的失败表现 |
| --- | --- |
| 10 | 旧 Python 合约的参数精确相等断言失败，包含 `20.0` 与 `20`、`[0.0,0.0,0.0]` 与整数数组、`2.0999999999999996` 与 `2.1`，以及螺母孔径的末位精度差异 |
| 6 | Quick 解析状态与断言不同，涉及缺少尺寸、蜗杆、显式圆周与线性阵列、弹簧垫圈及旧线性阵列别名的 ready、needs_clarification、unsupported 状态 |
| 3 | 连杆合约读取到缺少值后 unwrap 失败，裸目录名称的 warning 文案不匹配，以及内腔别名映射与断言不同 |
| 4 | schema 特征列表包含额外 `extrude_holes`、凸轮轴变体声明断言失败、参数边界探针列表缺少 `hex_bolt`，以及 EGT SW 工程的 `base` 布尔类型校验失败 |

这些分类依据实际输出，尚有参数、schema 和解析语义问题需要修复。当前迁移比较证明同名失败已存在于比较基线，适用范围限于此次环境与测试集合。[核心测试日志](../../temp/outputs/mygo-backend/core-tests.log) [基线测试日志](../../temp/outputs/mygo-backend/baseline-tests.log)

完整失败标识可用于后续定向复现。

```text
cad_plan_validation::tests::python_validation_and_strict_preflight_full_golden_corpus
cad_plan_validation::tests::quick_camshaft_faceted_journal_variant_is_scoped_and_schema_declared
native_feature_specs::tests::advertised_native_parameter_limits_are_exercised_by_preflight_and_builder
native_model_io::tests::f5_107_egt_sw_aimodel_marker_reopen_and_catpart_guard_fail_closed
native_quick::tests::missing_explicit_dimensions_returns_clarification_without_kernel
native_quick_parser::f5_46_legacy_conrod_contract_tests::legacy_conrods_match_python_oracle_defaults_units_and_boundaries
native_quick_parser::f5_47_legacy_cross_joint_contract_tests::legacy_cross_joints_match_python_oracle_defaults_holes_units_and_boundaries
native_quick_parser::f5_48_legacy_piston_contract_tests::legacy_pistons_match_python_defaults_and_record_parser_corrections
native_quick_parser::f5_49_legacy_worm_contract_tests::legacy_worms_match_python_formula_defaults_sources_and_explicit_boundaries
native_quick_parser::f5_55_legacy_spring_washer_contract_tests::legacy_spring_washers_match_flat_ring_recipe_and_report_fallbacks
native_quick_parser::f5_58_legacy_support_leg_contract_tests::support_leg_library_route_and_generic_high_fallback_match_python_entry
native_quick_parser::f5_59_legacy_connection_piece_contract_tests::connection_piece_recipe_matches_python_quick_fixture
native_quick_parser::f5_60_legacy_connection_block_contract_tests::connection_block_recipe_matches_python_quick_fixture
native_quick_parser::f5_61_legacy_nut_parser_tests::nuts_preserve_legacy_dimensions_and_report_geometry_corrections
native_quick_parser::legacy_catalog_cylinder_defaults::tests::exact_bare_catalog_names_reproduce_cylinder_defaults_and_provenance
native_quick_parser::legacy_pocket_aliases::tests::leaves_templates_compounds_existing_slots_and_whole_prompts_unchanged
native_quick_parser::tests::explicit_circular_pattern_matches_python_oracle_and_rejects_incomplete_requests
native_quick_parser::tests::explicit_linear_pattern_matches_python_oracle_and_rejects_incomplete_requests
native_quick_parser::tests::legacy_blade_and_blisk_templates_match_python_parameter_and_default_contracts
native_quick_parser::tests::legacy_flat_washer_recipe_matches_python_contract
native_quick_parser::tests::legacy_linear_pattern_aliases_match_python_quick_contract
native_quick_parser::tests::legacy_pocket_aliases_match_python_defaults_and_boundaries
native_quick_parser::tests::legacy_special_gears_match_python_parameters_defaults_and_fallbacks
```

63 项忽略测试仍按各自要求准备运行时与输入后单独执行。本次真实 OCCT 检查覆盖上文列出的模型及进程场景。

## 复现入口与待完成项

根目录 `npm run test:native` 依次运行 Go 依赖核验、vet、Go 测试、Rust 核心测试和 host 测试。任何步骤失败都会中止后续步骤，因此应记录实际执行到的位置。核心全量失败后，可独立复现宿主的三个场景，`test-support` 仅用于隔离 ACP 夹具。

```powershell
$env:AIMODELER_OCCT_MANIFEST = (Resolve-Path 'temp/build-cache/occt-runtime/runtime-manifest.json').Path
$env:AIMODELER_TEST_NODE = (Get-Command node.exe).Source
cargo test --locked --manifest-path src/apps/engine-host/Cargo.toml --features test-support --test jsonl -- --include-ignored
```

诊断、在线查询和旧 Vue Channel 检查保留兼容脚本。

```powershell
pwsh -NoProfile -File src/apps/desktop/scripts/test-native-diagnostics.ps1
pwsh -NoProfile -File src/apps/desktop/scripts/test-native-online-lookup.ps1
pwsh -NoProfile -File src/apps/desktop/scripts/test-agent-channel-vue.ps1
python -B -m unittest discover -s src/legacy/tests -p test_native_online_lookup_parity.py -v
python -B src/apps/desktop/src-tauri/examples/native_parts_catalog_parity.py
```

本次 Python 检查使用本机可运行的解释器，PATH 上的 Windows Store 别名不可用。复现时应选择真实 Python 可执行文件。Node 的 DSH 包检查需准备对应依赖，原生视口独立运行还需设置 `NATIVE_VIEWPORT_INTEGRATION`、`NATIVE_VIEWPORT_TEST_GLB` 与 `NATIVE_VIEWPORT_REPORT`。[窗口测试实现](../../src/apps/native-desktop/internal/viewport/integration_windows_test.go)

DSH 离线依赖已补入打包流程，并通过真实 Release 宿主在干净数据目录中的离线安装与重复安装检查。安装前 `bundled` 为 true，首次安装耗时 19.689 秒，安装后及重复安装后 `installed`、`verified`、`bundled` 均为 true。安装标记记录 `runtimeSource=bundled`，SHA-256 与包内归档一致。归档包含 28678 个文件，压缩大小为 198765604 字节，解压大小为 584505080 字节。[离线安装记录](../../temp/outputs/native-dsh-verification.json)

正常开发启动入口也已验证。执行 `npm run dev` 对应的 `run-native.ps1 -SkipBuild -Offline`，在启动边界取出其实际环境并启动 Release 宿主，完成 handshake、运行环境查询和正常关闭。干净数据目录返回 DSH `bundled=true`、`bundleRequired=true`、`state=missing`，OCCT `available=true`、`verified=true`。原 DSH 大包的修改时间保持不变，启动后环境变量恢复检查通过。[启动入口记录](../../temp/outputs/native-run-check-20261120-180321-699/verification.json)

CATIA 资源接入后，`--native-resources` 完成在线准备与离线重跑，复用已有 DSH 归档，生成固定版本的 Python 和 pywin32 资源。`Initialize-NativeRunResources` 将 `app`、`python`、`catia-bootstrap` 部署到 Release 宿主旁。重复离线初始化时，已部署 Python 可执行文件和 DSH 归档的修改时间保持不变。资源校验拒绝篡改的 Python 文件、篡改的 wheel 和越界清单路径，恢复有效文件后校验通过。[资源校验记录](../../temp/outputs/native-catia-resource-verification.json)

2026-11-20 18:52（UTC+8），`verify-native-catia.ps1` 使用上述 Release 宿主在独立临时数据目录中完成真实安装。先以 `长方体 20x15x8` 为输入执行 Quick，再请求 CATIA 导出到测试用扩展名，使宿主完成私有运行时准备后返回预期的扩展名错误。首次安装耗时 313 ms，安装标记中的 Python 版本、依赖清单哈希和 wheel 哈希均匹配包内清单，重复调用保持有效安装标记的写入时间。私有 Python 3.13.16 x64 成功加载 pywin32 312、`pythoncom` 和 `win32com.client`。证据记录了宿主与 CATIA 清单的 SHA-256，临时安装目录在验证后清理。[CATIA 隔离安装记录](../../temp/outputs/native-catia-verification.json)

本次 CATIA 注册表查询返回 `catia_registered=false`，证据中 `catia_export_verified=false`。实际 CATPart 导出、特征映射及推送到 CATIA 窗口仍需在已注册 CATIA COM 的环境中验收。外部 provider 的真实 Agent 请求和图片传输也待实际环境验收。早期中间包 `FormRecall-20261120-150423-953` 的 DSH invalid 记录保留为历史检查结果。[中间包检查](../../temp/outputs/native-package-model-check-20261120-150423/verification.json)

## 首轮交付包与异地启动

源码提交 `7dad64c3a568fe06331f652d93e573268eb6d4ef` 后执行完整离线构建和打包。交付包为 `temp/outputs/native-package/FormRecall-20261120-190810-974`，ZIP 为同目录下的 `FormRecall-20261120-190810-974-windows-x64.zip`，大小 231487132 字节，SHA-256 为 `38bbdb4db30540a2c86fb9078b760c6e45cde3a35d0791ad76b5b4234c5e456f`。[打包日志](../../temp/outputs/native-final-package.log) [包清单](../../temp/outputs/native-package/FormRecall-20261120-190810-974/package-manifest.json)

| 检查 | 实测结果 |
| --- | --- |
| 源码与 Go 构建信息 | `source_commit` 和 `vcs.revision` 都为 `7dad64c3a568fe06331f652d93e573268eb6d4ef` |
| 工作区状态 | 已跟踪文件无修改，35 个既有未跟踪实验文件使 `source_dirty` 和 `vcs.modified` 为 true |
| 目录和 ZIP | 104 个清单文件大小和 SHA-256 全部匹配，两个可执行文件为 Windows x64 |
| Go 桌面 SHA-256 | `dcaf608390ac2c58706e50dd7aec5557778ad7388bc862a25dbcdb85f85c5772` |
| Rust 宿主 SHA-256 | `398a64f4e2df992fb179b55abae1995bea68283933bb085d8b27ae9030a1b2a9` |
| 包内 DSH 安装 | 干净数据目录安装耗时 25622 ms，重复安装通过 |
| 包内 CATIA 私有运行时 | 安装耗时 355 ms，重复准备及 Python/COM 模块加载通过 |
| 异地解压 | 在仓库外含空格和中文的临时目录解压 ZIP，包内校验通过 |
| 实际桌面入口 | 清理相关环境变量，直接启动异地 `FormRecall.exe`，由程序定位同目录宿主和资源 |
| 工作台与设置 | 两次独立启动均完成四孔底板建模，尺寸 100×80×10 mm，2 个特征、604 个三角形，视口错误为空 |
| 退出 | 工作台、设置截图进程和独立宿主均返回 0，残留进程为 0，异地临时目录已清理 |

最终界面截图、状态 JSON 和构建信息随异地验证保存，程序源码与构建版本以包清单中的提交为准。[验证记录](../../temp/outputs/native-final-relocation/verification.json) [工作台截图](../../temp/outputs/native-final-relocation/workbench.png) [设置截图](../../temp/outputs/native-final-relocation/settings.png)

## 桌面视口空白修复

用户验收首轮程序时，Quick 已生成特征和网格，桌面视口却持续空白。新增回归直接读取隔离测试窗口所在客户区的桌面像素，覆盖初始化、缩放、最大化、还原、设置隐藏恢复、模型重载和相机切换。修复前初始场景的 OpenGL 已呈现前缓冲有 142813 个模型亮像素，桌面对应区域为 0，复现了用户看到的问题。[失败证据](../../temp/outputs/native-presentation/before-fix/presentation.json) [失败截图](../../temp/outputs/native-presentation/before-fix/initial-window.png)

原 WGL 子窗口挂在 MyGoSurface 下，界面的交换链呈现覆盖了其桌面显示。修复将 WGL 视口改为应用根窗口的直接子窗口，与 MyGoSurface 同级，并在布局和设置关闭恢复时维护层级。布局经 `MapWindowPoints` 映射，保留 DPI 缩放与实际客户区位置。模型解码和几何生成继续走现有入口。[实现](../../src/apps/native-desktop/internal/viewport/native_windows.go)

新回归独立读取桌面像素与已呈现的 GL_FRONT，并核对 HWND 实际可见性及模型像素一致性。录屏也改为 `DwmFlush` 后从桌面读取测试窗口客户区。此前 `CaptureWindow` 合成截图的验证范围已在本记录中更正。[桌面回归](../../src/apps/native-desktop/internal/viewport/presentation_windows_test.go) [录屏实现](../../src/apps/native-desktop/internal/workbench/ui_motion_windows_test.go)

使用用户已生成的 200×150×12 mm 四孔板 GLB，修复后的 8 阶段回归通过，7 个显示阶段的模型像素与对应前缓冲一致，隐藏阶段的 HWND 可见性符合预期。初始桌面捕获到 143832 个模型亮像素，最大化为 697510 个，关闭遮罩恢复后为 125942 个。测试窗口禁用外部键鼠输入，桌面与前缓冲在同一次 UI 线程调用中读取，并断言采样期间渲染帧数稳定。[用户模型回归](../../temp/outputs/native-presentation/user-model-fixed/presentation.json) [回归日志](../../temp/outputs/native-presentation/user-model-fixed-test.log)

修复后的实际桌面录屏包含 17 个阶段，所有目标状态和逐帧状态稳定检查通过。16 个动画阶段在前 450 ms 捕获到 5 至 8 次显著变化，初始静止阶段为 0 次。显著变化按 RGB 通道差超过 8 且达到最小变化面积计数，降低合成器细微像素变化的干扰。视频为 1598×1038，实际采集 577 帧，平均 30.01 帧/秒，完整解码得到 577 帧、19.189 秒。[实际桌面视频](../../temp/outputs/native-ui-motion/desktop-verified/native-ui-motion.mp4) [逐帧记录](../../temp/outputs/native-ui-motion/desktop-verified/recording.json) [视频解码](../../temp/outputs/native-ui-motion/desktop-verified/decoded.json)

修复后 `go vet -mod=readonly ./...` 通过，普通 Go 测试 60 项通过、5 项按环境条件跳过、0 项失败。桌面显示与录屏测试按上述方式另行显式启用。既有 Rust 全量基线限制见前文。[Go 测试记录](../../temp/outputs/native-presentation/go-tests-final.jsonl)
