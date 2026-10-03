# Rust + DSH 重构总 Todo

更新时间：2026-10-04。每一项 feature 按边界实现、验收并单独提交。首个“目录重构”feature 完成后直接提交到 `main`；后续 feature 使用独立提交，不把未验收工作合并。

## 总目标

桌面程序启动整套建模流程：Rust/Tauri 管理应用、runtime 与 CAD 状态；DSH Agent 自主决定是否以及如何调用 DSH 原生 Cordis 工具；Rust 负责 CADPlan 与几何核心；Python 最终只保留 CATIA COM。保留快速模式，并允许快速模式与 Agent 创造模式切换。

## 当前进度

| ID | Feature | 状态 | 完成条件 / 提交边界 |
|---|---|---|---|
| F0 | 目录重构、文档重建和路径恢复 | **进行中（目录迁移完成；Vite headless 验收完成；Tauri 实际启动未验收）** | 根目录现有 package.json：根目录 `npm run dev` 启动 Tauri，`npm run dev:web` 才启动 Vite；桌面包目录 `npm run dev` 是 Vite，`npm run tauri:dev` 启动 Tauri。2026-10-04 Playwright CLI 对合并前 main b597a69 的 Vite 页面快照能看到完整界面，但状态错误地显示后端未连接且 Console 有 favicon 404；集成分支同命令显示浏览器预览已就绪、桌面操作禁用、Console 0 error/0 warning。合并后 Vite HTTP、Vue build、tauri-cli 2.12.1 与 `tauri:dev -- --help` 均通过；实际 Tauri `beforeDevCommand`、WebView2、Rust IPC 初始化与桌面首屏仍未运行，用户报告的桌面无界面症状未在 Vite headless 预览中复现。见 [Tauri dev 与 Vite 启动核查](../05-operations/03-tauri-dev-cli-preflight.md)。 |
| F1 | Rust DSH ACP Host + 固定 Runtime Manifest | **已完成** | F1.1–F1.4 已分别实现、验收并单独提交；多模态识别准确率问题保留为 F7 质量验收项 |
| F1.1 | Rust ACP stdio 进程与 JSON-RPC 基础 | **已完成** | 行分帧、并发请求响应匹配、通知订阅、错误/超时、关闭时有界回收；以假 ACP 子进程测试，不依赖真实 DSH |
| F1.2 | 固定 DSH/Node Runtime Manifest 与安装器 | **已完成** | 固定已验收的 Node 与 DSH 包版本/校验值；下载、校验、安装、升级、失败回滚，不依赖全局 Node |
| F1.3 | Rust Host 会话、图片与 Tauri Agent 路由 | **已完成** | Agent 模式由 Rust ACP Host 持有会话，支持图片、多轮恢复、取消与关闭；thought/tool 流实时进入 UI；Quick 仍使用 Python。固定 DSH 的无模型 ACP 控制面通过 |
| F1.4 | DSH ACP 真实运行验收与恢复边界 | **已完成（链路通过，识图错误转 F7）** | 固定版本 CLI profile 已真实跑通图片 ACP 输入、Cordis 只读工具、恢复、取消和退出；实际识图把 6 孔判成约 8 孔，记录为 F7 负样本 |
| F2 | DSH 原生 Cordis CAD 插件去 Python worker | **已完成（Rust 路由）** | 当前 25 项 Cordis 工具均有 Rust 路由；CAD 状态、建模、视觉和元数据不再回退到 Python tool worker；真实 DSH 调用 `cad_apply_recipe` 与 OCCT 输出通过。多模态识图质量继续归 F7 |
| F2.1 | Cordis → Rust 本机工具桥 | **已完成** | Cordis 原生注册与 Rust Host 的认证 JSON-RPC、取消、超时、去重和图片返回链路测试通过；Rust 下游仍调用旧 Python worker，故不代表 F2 完成；提交 `501d47b` |
| F2.2 | Rust 持有 CAD 工具 schema 与参数契约 | **已完成** | Rust 内嵌 25 项 schema，与迁移时 Python 函数输出逐字段相同；不再启动 Python 取 schema；固定 DSH `defineTool()` 成功注册 25 项 |
| F2.3 | 只读状态、知识及联网工具迁入 Rust Host | **已完成** | 六项原生读取工具已接入 Rust；生产测试 55/55，实际 DSH CLI 完成五项调用且禁止 Python 回退；外网搜索因本机 DNS 非公网解析失败，如实返回，正文成功路径以 HTTP fixture 验证 |
| F2.4 | 图片观察与视觉复核迁出 Python | **已完成（原生接线）** | domain/executor 原生接线、真实 DSH 观察工具及原图传输完成；观察精度与完整复核质量由 F7 独立验收 |
| F2.4.1 | Rust 多模态 provider 与图片资产 | **已完成（传输通过，观察质量未通过）** | 双协议、取消、SHA 遥测、EXIF/ROI 输入已实现；真实原图请求成功，观察错误作为后续负样本保留 |
| F2.4.2 | 原生视觉 domain 与 Cordis 接线 | **已完成（接线/传输）** | 真实 DSH cad_inspect_references、SHA、Responses、禁止 Python fallback 通过；单视图复核为 fixture 验收，不冒充质量通过 |
| F2.5 | CAD 写工具迁移到 Rust CAD domain | **已完成** | 25 项 Cordis schema 全部有 Rust 路由；原生 DSH 配方调用、CADPlan 写入、OCCT 构建和多格式输出通过；Agent Host 与 Cordis bridge 无 Python worker/fallback，残留的死 worker 入口已删除 |
| F3 | Rust CADPlan 与增量模型事务 | **已完成** | 事务、校验、会话版本、候选恢复、复核门禁、OCCT preview/build dispatch 已接线并通过 CLI/Cordis 验收 |
| F3.1 | Rust CADPlan 纯增量事务与几何指纹 | **已完成** | create/add/update/remove/snapshot 保留来源与扩展字段；数值更新来源 guard、失败原计划不变；140 组 Python 几何指纹对拍一致，精确浮点解析已启用 |
| F3.2 | Rust CADPlan schema 与静态校验 | **已完成** | 覆盖 27 类特征、条件参数、来源、关系、单位/坐标和静态支持状态；27 个 Python golden 双接口对拍通过，strict 执行前阻断已验证，未执行几何或 CATIA |
| F3.2.1 | 布尔 base 校验迁移缺陷修复 | **已完成** | 实际几何接线暴露合法 base 被误当长度拒绝；补 8 个真实 Python 校验样例，Rust 对拍通过 |
| F3.3 | Rust CAD 会话事务与 preview/build dispatch | **已完成** | Rust 会话事务连接原生 OCCT backend；create/add/update/remove/preview/build 和候选状态完成本地 CLI 验收，回滚需关联当前 finding ID |
| F3.3.1 | 会话事务及版本/来源/验收门禁 | **已完成（domain）** | 16 项定向正负测试通过；真实后端及 Cordis 写工具接线尚待完成，复杂来源语义仍有限制 |
| F3.3.2 | 宿主整体复核与完成门禁 | **已完成（domain）** | session 18/18、gate 7/7；旧版本/篡改证据阻断，当前未验收候选可保存；真实视觉质量待 F7 |
| F4 | Rust/C++ thin OCCT FFI | **进行中** | 固定官方 OCCT 7.9.3 SDK、C ABI/Rust 包装及本机 packaged probe 已通过；执行 registry 当前覆盖 27 类规格中的 21 类，另 6 类及已实现类的拒绝变体、最终安装包/干净机验收仍未完成 |
| F4.1 | 固定 OCCT SDK、C ABI 与 Rust 几何基础 | **已完成** | 实际 Rust → C ABI → OCCT fixture 通过，正常退出；原生 27 类 CADPlan 执行及安装交付仍待完成 |
| F4.2 | CADPlan 基础特征到 OCCT 的原生执行 | **进行中** | 已接入 box/cylinder/sphere/hole/pocket、cone/prism/revolve、gear/helical_gear/bevel_gear/socket_head_bolt、pattern、piston、camshaft、crankshaft、timing_pulley、vbelt_pulley 等特征；其余特征映射与安装 runtime 仍未完成 |
| F4.2.1 | 五类基础特征与参数坐标对拍 | **已完成** | box/cylinder/sphere/hole/pocket 的基础变体已执行；24 组真实几何对拍、4 个负样本通过，孔记录未冒充独立测量 |
| F4.2.2 | 圆锥、棱柱和回转能力 | **已完成** | 三类原生构造器及单位/坐标接线完成；含负角回转的 44 组旧内核几何对拍通过 |
| F4.2.3 | 齿轮与螺钉原生几何执行 | **已完成（标记近似）** | 完整 CADPlan 接入 gear/socket_head_bolt；无尺寸时拒绝兜底；几何包围盒/体积对拍，螺纹近似 warning |
| F4.2.4 | CADPlan 线性与圆周 pattern 原生执行 | **已完成** | 复制紧邻来源特征的原生 B-Rep 刀具；全局 XYZ/右手圆周变换，孔轴方向与孔位同步变换；Rust/Python OCP 54 正例、9 负例对拍；证据见 [F4.2.4 验收记录](./22-f4-native-pattern-execution.md) |
| F4.2.5 | CADPlan piston 原生几何执行 | **已完成** | 保留旧活塞剖面、环槽和 X 轴销孔语义；Rust/Python OCP 57 正例、12 负例全量对拍通过（本项新增 3 正例、3 负例）；CATIA 映射仍为 approximate，证据见 [F4.2.5 验收记录](./23-f4-native-piston-execution.md) |
| F4.2.6 | CADPlan spoked_wheel 原生几何执行 | **已完成** | 全局 Z 轴轮缘、带孔轮毂、辐条及 cm 单位换算；真实几何全量 60 正例/15 负例通过；见 [F4.2.6 记录](./24-f4-native-spoked-wheel.md) |
| F4.2.7 | CADPlan conrod 原生执行与平移修复 | **已完成（标记近似）** | 修复旧Python杆身重复平移，Rust环形头+梯形杆身；63正例/18负例对拍，平移不变性与cm换算；见 [记录](./31-f4-native-conrod.md) |
| F4.2.8 | CADPlan cross_joint 原生执行 | **已完成（标记近似）** | OCCT 中心块与四轴颈解析融合；有效单闭合体及轴心材料区间检查；66正例/20负例；与旧24段网格体积差0.95857%，不作精确体积对拍；见 [记录](./33-f4-native-cross-joint.md) |
| F4.2.9 | CADPlan rotor 原生执行 | **已完成（标记近似）** | 固定径向离散轮廓经Rust→C ABI→OCCT构造，拒绝隐式尺寸/非Z轴/base及不满足实际内切间隙的孔；5个真实B-Rep、3个定向测试、能力规格2项及offline cargo check通过；见 [记录](./41-f4-native-rotor.md) |
| F4.2.10 | CADPlan camshaft 原生执行 | **已完成（旧 facet 体积仅报告）** | 48点凸轮截面与轴颈经 Rust→C ABI→OCCT 构建；3个有效单闭合 B-Rep 与3个负例通过；包围盒误差0 mm，旧 faceted B-Rep 相对体积差0.18146%，仅报告不作精确体积对拍；见 [记录](./60-f4-native-camshaft.md) |
| F4.2.11 | CADPlan crankshaft 原生执行 | **已完成（CADPlan OCP B-Rep 精确对拍）** | X轴主轴/交替偏心轴颈/矩形曲柄臂经 Rust→C ABI→OCCT 构建；3个有效单闭合 B-Rep 与3个拒绝负例；对旧 Python OCP B-Rep 包围盒差0 mm、体积差≤2.33e-10 mm³。Document polygon-only 网格转 B-Rep 因自由边失败，不宣称 facet 体积通过；见 [记录](./61-f4-native-crankshaft.md) |
| F4.2.12 | CADPlan helical_gear 原生几何 | **已完成（标记近似）** | 保留 Document 直梯形齿语义（helix_deg 忽略、bore_d=0 无孔、显式孔 40 边）；该特征 3 个正例、2 个负例通过；并入曲轴后的集成对拍为 75 正例、28 负例，bbox 最大误差 0 mm，helical 显式孔体积绝对误差 3.04e-10 mm³；CATIA approximate；见 [F4.2.12 验收记录](./62-f4-native-helical-gear.md) |
| F4.2.13 | CADPlan bevel_gear 原生执行 | **已完成（旧 facet-BREP 精确对拍，仍标近似）** | Rust 复刻当前 Document 梯形齿/线性锥缩网格并缝合为有效单闭合 B-Rep；新增 3 个正例包围盒差 0 mm、体积差 0 mm³，8 个负例拒绝；完整集成 OCP/Rust 对拍 78 正例 / 36 负例通过。这不是渐开线齿面或解析锥齿轮，CATIA 映射仍省略锥缩。详见[记录](./62-f4-native-bevel-gear.md) |
| F4.2.14 | CADPlan timing_pulley 原生执行 | **已完成（近似几何）** | 仅支持有径向材料重叠的 MXL/XL/T2.5/T5/3M；拒绝九种无法保证单闭合体的高齿 profile。旧 OCP parity 新增 3 正例、7 负例，完整 runner 81 正例/43 负例通过；三个正例 bbox 最大差 8.9e-16 mm，体积相对差 0.02286%–0.22409% 仅报告且处于旧分段盘体误差边界内。8M legacy OCP 自交拒绝；不含 CATIA 验收。详见[记录](./63-f4-native-timing-pulley.md) |
| F4.2.15 | CADPlan vbelt_pulley 原生执行 | **已完成（标记近似）** | fallback 标准槽轴向 profile 经 Rust→C ABI→OCCT 回转；feature-only 4 正例/4 负例通过，bbox 与体积误差均为0。集成分支完整矩阵 **86 正例 / 47 负例** 通过（5 个 V-belt 正例、3 个 timing 正例），bbox 容差 `1e-6 mm`、体积容差 `1e-5 mm³`。窄轮宽、辐条结构、要求 legacy 静默缩孔的变体拒绝。当前 `native_vbelt_pulley` 模块 3 项、native capability registry 6 项通过；CATIA 仍省略 V 槽。此结果是 CADPlan 几何对拍，不是 Agent 图像验收。详见[记录](./63-f4-native-vbelt-pulley.md) |
| F4.3.1 | B-Rep 材料区间与离散孔证据基础 | **已完成** | 精确面交事件、Rust RAII、41 射线 pre/post 对比；真实九孔/盲孔/错位/错径正负样例通过，非孔径计量，生产接线待完成 |
| F4.3.2 | 孔材料采样接入 CADPlan | **已完成** | 44 对拍、6 负样例、局部材料盲孔与近边孔部分证据通过；采样不冒充孔径计量 |
| F4.3.3 | 最终 B-Rep 孔计量接入 CADPlan | **已完成** | 最终形体实测孔半径、轴线与 360° 覆盖；盲孔/贯穿孔核验，后续特征会使旧测量失效 |
| F4.4.1 | 精简 OCCT 资源组装与定位 | **已完成** | 17 DLL 哈希校验打包，Node 2/2、Rust 7/7；系统 PATH 下实际 packaged probe 通过，最终安装包/干净机器仍待验收；见 [记录](./26-f4-packaged-occt-runtime.md) |
| F5 | Quick 模式与本地导出迁移 | **进行中** | Rust Quick、活动模型路由、AIMODEL/STL/GLB、七视图、原生 OBJ 和 CATIA COM adapter 已有实现与定向验收；CATIA 仅完成 mock COM，真实 CATIA 几何仍待验。F5.5 已恢复 flange、stepped shaft、sleeve 模板子集及禁用特征状态传递；F5.6 增加法兰中心孔“孔径”别名兼容；airfoil/loft 明确拒绝，其他旧语法兼容和 Quick/Agent 端到端仍待完成 |
| F5.1 | Rust 七视图 PNG 与深度遮挡 | **已完成** | 五项定向测试、真实 OCCT 1000 三角形 CLI 七视图通过；非 Agent 图片验收 |
| F5.2 | Rust AIMODEL / binary STL 兼容读写 | **已完成** | 5 项单测、旧工程 roundtrip、Python Document 对拍及实际 STL 全顶点检查通过；Quick 写出接线由 F5.3 完成，详见 [Quick 创建记录](./27-f5-native-quick-build.md) |
| F5.3 | Quick 明确尺寸原生创建闭环 | **已完成（限定范围）** | Rust Quick 3/3、解析 7/7、Vue build；CADPlan→OCCT→GLB/AIMODEL/STL，旧语法全量迁移和共享活动状态仍待 F7.G2；见 [记录](./27-f5-native-quick-build.md) |
| F5.4 | Rust 原生活动模型 Wavefront OBJ 导出 | **已完成，提交 `a07a229`** | Quick 与 DSH Agent 从 Rust/OCCT 网格写 OBJ；活动模型导出使用当前 generation 路径，包含三角面、法线和毫米坐标。无效输入不覆盖目标文件；Rust model I/O 7/7、Quick 实建 3/3，详见 [记录](./40-f5-native-obj-export.md) |
| F5.5 | Quick 旧模板子集与禁用特征兼容 | **已完成（限定范围，提交 `c068c6f`）** | `flange`、`stepped_shaft`、`sleeve` 迁入原生 Quick；`airfoil`/`loft` 无支持映射时显式拒绝。`enabled` 在 CADPlan/Document 转换、工程保存、活动特征切换中保留，禁用项不参与几何。Rust parser 9/9、CADPlan validation 8/8、disabled-feature 4/4、active import 2/2、Python SDK 24/24；不代表完整旧语法或端到端完成。见 [验收记录](./F5-quick-legacy-compat-acceptance.md)。 |
| F5.6 | Quick 法兰“孔径”中心孔别名 | **已完成（限定语法）** | Rust Quick 法兰模板兼容 Python Quick 的“孔径”参数别名；仅覆盖法兰模板的中心贯穿孔直径，不扩展到普通零件解析或套筒。Python parser 对拍输入加入 golden fixture，Rust parser 9/9 通过；见[记录](./F5.6-quick-flange-hole-diameter-alias.md)。 |
| F6 | Python 缩减到 CATIA COM | **进行中（F6.7 实现与部分验收完成）** | Agent Python tool worker 与桌面 UI backend bridge 已移除；F6.1–F6.6 原生设置、bootstrap/readiness、零件目录、诊断/路径、联网查询及按需 CATIA COM adapter 已实现。F6.7 已将安装资源收缩到 CATIA 按需 Python/pywin32、DSH/提示词/知识库/文档及 OCCT；Quick 私有路径冷启动、285/285 Rust 测试 wrapper 和 MSI/NSIS 编译通过。完整 Agent 在无全局 Python/Node 下冷启动和真实 CATIA COM 验收仍未完成；测试 wrapper 已覆盖 Rust 测试套件。 |
| F7 | 多模态盲测与科学验收基线 | **进行中** | Run03–10八次真实单图冷启动失败；Run11在读图前被 runner 拒绝。Run12验证图片返回修复。Run13原图/预览解码和附件保存成功，current_geometry、artifacts、holes 通过；ACP重复终态、visual coverage/original-reference binding、finish 仍失败。Run14 使用原图、DeepSeek `deepseek-flash` / low 和隔离 DSH home 完成单次 CLI 运行；transport/DSH、原图、主图 wire、current_geometry、artifacts、holes 通过，Cordis 调用/审计、ACP tool result、visual 与 finish 失败。allowlisted projection 汇总 24 个 CAD 调用组，其中 11 组有重复同类终态更新、1 组失败终态、missing-ID event 为 0；投影不能判定重复原因，严格 gate 未放宽。详见[Run14](./59-f7-g8-run-14-diagnosis.md)。Run15 已核实原图与隔离配置前置条件并准备固定 Node/DSH，但执行器在 runner CreateProcess 前以 `blocked by policy` 拒绝单次 CLI 启动；没有发送 provider 请求、没有 Run15 gate 结果。详见[Run15 preflight](./60-f7-g8-run-15-preflight.md)。G3 Channel→Vue四场景headless矩阵、G4移除特征finding回归及manifest-aware Rust测试通过。真实WebView IPC、视觉复核与差异返修、多输入/多轮/取消、桌面运行态和CATIA分层验收未完成，F7不得标记通过。见[Run12](./55-f7-g8-run-12-diagnosis.md)、[Run13](./56-f7-g8-run-13-diagnosis.md)、[Run14](./59-f7-g8-run-14-diagnosis.md)和[Run15 preflight](./60-f7-g8-run-15-preflight.md)。

Run15 preflight 随后的静态审查发现旧 runner 会持久化 raw tool trace、临时 ID、answer 与 thought 长度。隔离 worktree 已完成安全发布修复：默认且唯一发布 safe gate summary、ACP v2 HMAC allowlist projection、Cordis v1 allowlist counts 与 build status；55/55 合成测试通过；发布层进一步固定 gate-name allowlist、status enums，并仅输出 reason counts，scope/runtime 使用固定值校验与 fallback。运行期间 scratch 仍依赖系统临时文件并在正常/常规退出时清理，异常强杀/断电残留风险已记录。该修改尚未通过主线程审阅，也没有真实 DSH/provider 重跑，F7/G8 仍未验收。

## F6 子任务与验收顺序

| ID | 优先级 | Feature | 完成条件 / 提交边界 |
|---|---|---|---|
| F6.1 | P0 | Rust 原生设置与 provider 管理 | **已完成，提交 `16756a3`**：原生 defaults、旧 flat 配置迁移、多档案与 stage overrides、活动档案、未知字段保留、模板及 `/models` 请求；Rust 7/7、Vue wiring 8/8、前端 build 通过。 |
| F6.2 | P0 | Rust 桌面 bootstrap 与依赖状态 | **代码已提交 `3e8b247`**：Vue 使用原生 settings/runtime/resource/active-model 命令，不再握手/初始化 Python；资源根不要求 Python 源码；DSH 缺失不阻塞桌面、Quick 与本地活动模型，仅用户发起 Agent 时进入 DSH 安装；DSH/OCCT/CATIA/Python 状态分开显示。`native_bootstrap` 3/3、`native_active_model` 7/7、Vue wiring 2/2、前端 build 通过。未开 GUI，物理无 Python 安装包冷启动留待 F6.7。依赖 F6.1。 |
| F6.3 | P1 | 原生零件目录与示例列表 | **已完成，提交 `6e48a0e`**：Rust 原生命令和 Vue 直连迁移 `parts.list`/`samples.list`；与旧 Python `Bridge.dispatch` 实例逐字段对拍，66 个零件行、17 个示例及 4 种筛选完全一致，涵盖类别、别名、参数、样例值/提示、计数、顺序。Rust 3/3、Vue 2/2、`npm run build` 通过。快照是静态资源，源 metadata/KB/sample 更新后需重建并重跑 parity；目录条目不代表几何内核支持。详情见 [F6.3 验收记录](./55-f6.3-native-parts-catalog.md)。 |
| F6.4 | P1 | 原生诊断、帮助和路径操作 | **已完成，提交 `1882053`**：版本、自检与资源目录内帮助路径由Rust处理；分开显示DSH、OCCT、CATIA COM、Cordis schema和外部MCP状态，不虚报进程/连接。Rust diagnostics 4/4、bootstrap 3/3、Vue 3/3、`npm run build`通过；未开GUI。 |
| F6.5 | P1 | 桌面手工联网查询 | **已完成，提交 `323f569`**：Rust 原生复刻旧 `online.ask` 的四级 query 扩展/顺序、两层相关性筛选与首批结果回退、前两条正文读取、尺寸参数和标准件 suggestion；Tauri/Vue 手工查询已移除 Python bridge。12 组 Python golden fixture 字段与调用顺序对拍，Rust 3/3、query-relevance 本地 mock 1/1、Python parity 5/5、Vue wiring 3/3、生产前端 build 通过；无真实公网、DSH 或 CATIA 验收。见 [F6.5 验收记录](./56-f6.5-native-online-lookup.md)。 |
| F6.6 | P2 | 按需 CATIA COM adapter | **已完成（headless/mock 验收）**：只有 CATPart 导出或推送命令才启动最小 Python adapter；输入是 Rust 活动模型的 F5 `ai-modeler` v1 工程、generation/revision/fingerprint 与 CADPlan 来源证据，不调用旧 Python `Document`。结果和 sidecar 对每特征标记近似映射或跳过项，并声明未验证 CATPart 几何。Python mock 4/4、Rust adapter 3/3、前端 build 通过；真实 CATIA COM/B-Rep 未验。详见 [F6.6 验收记录](./57-f6.6-catia-com-adapter.md)。 |
| F6.7 | P2 | Python 依赖与安装包收缩 | **实现和构建验收完成，真实环境验收仍进行中（提交 `0c55a63`、`1630533`、`be9e641`）**：移除了当前 Tauri UI 的 Python backend bridge 和通用 Python backend/bootstrap 资源；CATIA Python/固定 pywin32 改为用户触发时放入应用私有 runtime。资源 staging 6.69 MB/91 文件、OCCT 17 DLL 校验通过；Quick 生产 CLI 在 PATH 仅含 System32、无 Python/Node 环境下成功并产出 AIMODEL/GLB/OBJ/STL。2026-10-04 `test-lib.ps1 -Offline` 全量 Rust **302/302 通过**，Vite build 通过；`npm run tauri:build` 生成 MSI 与 NSIS。剩余：安装后的无全局运行时 DSH Agent 冷启动和目标机真实 CATIA COM 导出/推送；安装包尚未在干净账户安装验收。见 [F6.7 记录](./59-f6.7-python-resource-split.md)。 |

当前审计依据：`feat/rust-dsh-acp-host` 的实际源码。F6.1 与 F6.2 是解除桌面常驻 Python 门槛的 P0；F6.3 已按旧 Python 输出对拍完成。联网和外部 MCP 仍需保留各自旧契约，不以同名 Rust 工具存在视为迁移完成。

## 已确认的 Agent 链路治理

2026-10-03 用户确认：以下八项全部纳入 Todo，**P0 优先**。感知、建模、审查允许分别选择模型/provider，默认共用当前配置；建模优先短推理，复杂识图与审查按需增强。继续使用 DSH 原生 Cordis 插件。详细依据与验收见 [Agent 链路治理卡](./25-f7-agent-chain-governance.md)。

| ID | 优先级 | 任务 | 状态 | 完成条件 |
|---|---|---|---|---|
| F7.G1 | P0 | 主 Agent 直接查看原图和当前单视图 | **原图/单视图附件传输及 ACP 多图构造回归通过；真实视觉理解/验收仍失败** | Run12 原图 SHA `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`；`cad_get_reference_image` 的 10/10 Cordis result 为 `isError=false`，`image/png` 经 native decode 和 `attachment_save` 均成功；`cad_preview_model` 2/2 同样成功，且 `main_image_wire` passed。`082097b` 新增实际生产 helper 测试，核对多张 ACP image part 的顺序、MIME、Base64 解码字节与 active-turn manifest SHA 一一对应；1 passed、291 filtered。独立视觉 review mock 也逐字节断言原图+当前单视图预览传输。上述仅证明 ACP/视觉工具载荷与 provenance 接线，不证明真实 DSH 收到图后理解正确。Run09 verification 仍 unknown；`visual` gate 仍报 `visual_coverage_incomplete_or_conflicting`、`visual_gate_original_reference_binding_missing`，视觉验收不能标通过。Headless predicate 翻转回归覆盖 visual coverage 6 项、原图 binding 6 项、host finish 7 项；native_tool_executor 集成负例现覆盖 source SHA 错绑、错误 reference ID、仅 ROI 库存、空/重复 evidence、旧 observation 刷新、partial stage、mismatch 与 unsupported coverage。测试发现并修复 unsupported_coverage_ids 在 cad_get_state 汇总中丢失、导致错误提升为完整 coverage 的 fail-open；现由 executor→cad_get_state 回归锁定。设置项目 IPC 测试所需 Node/ACP fixture 后，全量 Rust 单测 302/302 通过。真实单/多图视觉理解与正确复核仍待真实 DSH/provider 原图验收；见 [executor 库存绑定负例](./65-f7-g1-executor-inventory-binding.md) 与[Run09](./51-f7-g8-run-09-diagnosis.md)、[Run12](./55-f7-g8-run-12-diagnosis.md)、[ACP多图构造回归](./64-f7-g1-acp-image-prompt-regression.md)与[visual/finish evidence audit](./58-f7-visual-finish-evidence-audit.md)。 |
| F7.G2 | P0 | CADPlan、Agent 与桌面模型统一同步 | **隐藏 WebView2 → Rust Channel → 生产 Vue handler 四场景 CLI 验收通过；桌面渲染与真实 DSH 同步仍待验收** | Agent/Quick/特征编辑、活动模型恢复、Save As、STL/OBJ/report/多视图快照和显式旧工程转换都路由到活动模型；Agent 几何工具通过 generation CAS 防止旧会话覆盖。a4dfe2b 验证 Agent 编辑发布共享 generation 并同步会话快照；a2cc562 修复 Vue reactive settings 序列化问题。`c60e92e` 覆盖 Host→fake ACP→本地事件 sink；`8cbdaab`、`3b3f119` 增加 `npm run test:agent-channel-vue`，由 Tauri MockRuntime 调用共享 `agent_send` 核心、捕获 Rust `Channel<Value>` JSON，再交给生产 Vue Channel callback；验证 model revision/document/feature、request id 和 terminal candidate。`1b13d1e` 新增 smoke-only Tauri binary 和 CLI WebDriver harness；首次隐藏 WebView 运行中纯问答、工具失败通过，accepted-current 因 fixture 漏 `acceptance_confirmed` 超时；`03080f1` 修复 fixture 后用匹配 Runtime 的 EdgeDriver 和本地 `tauri-driver` 唯一复跑 4/4 场景通过。该 smoke 只证明 production bundle 的 WebView→Channel→Vue 投递，不等于真实 DSH ACP、桌面布局/像素/WebGL 或用户体验验收。启停与 CATIA 原生导出仍明确受限。见[活动模型同步记录](./38-f7-active-model-sync.md)与[WebView harness 说明](./62-f7-webview-driver-harness.md)。 |
| F7.G3 | P0 | 请求结束与模型验收状态区分 | **Host finish 新鲜度与接受条件 headless 翻转矩阵、Vue 矩阵及真实隐藏 WebView 四场景通过；真实 DSH 运行态仍待验收** | `1dd79b5` 的四场景 Channel→Vue 矩阵覆盖纯问答、工具失败、当前 accepted、旧 revision stale accepted；`1b13d1e` 增加隔离 WebView2 CLI harness；首次 accepted-current fixture 漏字段失败，`03080f1` 修复后真实隐藏 WebView2 四场景 4/4 通过。`26e3362` 将 finish acceptance 绑定到 canonical evidence snapshot：same revision 下视觉库存/复核、原图 manifest、finding、外部事实或几何检查变化即失效，证据不变仍 accepted；Vue 只接受 host `acceptance_confirmed=true`。本次新增 host session 负例覆盖 local geometry、reference coverage、construction milestone、required external facts 四项；既有用例覆盖整体 geometry review、孔测量和开放 finding，逐项阻断时保持 `accepted=false`。另补两条跨 gate 独立性负例：视觉全覆盖但 open finding 阻断 finish；finish summary 自称 accepted 但原图 reference row SHA 错绑时 host summary 回落为 candidate。经仓库 Windows test wrapper `src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline`（设置项目 IPC 所需 Node/ACP fixture）全量复跑，302/302 通过，包含上述两条负例。先前绕过 wrapper 直接启动测试 exe 曾报 `STATUS_ENTRYPOINT_NOT_FOUND`；wrapper 为测试进程提供 Common Controls v6 sidecar manifest。mock ACP/headless 不等于真实 DSH/WebView/视觉质量；用户可见状态及真实多模态运行仍待验收。见[状态真值](./29-f7-turn-and-model-status.md)、[可见状态记录](./42-f7-g3-visible-acceptance-state.md)、[finish证据绑定记录](./51-f7-finish-evidence-binding.md)、[visual/finish evidence audit](./58-f7-visual-finish-evidence-audit.md)与[WebView harness说明](./62-f7-webview-driver-harness.md)。 |
| F7.G4 | P0 | 阶段复核与施工范围一致 | **宿主成功构建绑定、production normalize→reconcile 回归与移除特征负样例通过；真实视觉返修待验收** | `verified_stage_scope` 现在要求当前 revision/plan fingerprint 对应的 `last_preview` 为 passed，并验证 `native_cad_session.complete_preview` 的报告 SHA attestation；Agent milestone 自报不能独立证明 feature 已施工。enabled hole 自报 completed、但无成功构建 attestation 时 provider 前拒绝；native_vision_domain 19/19、native_review_findings 5/5、native_executor_inspect_current_review_and_get_state_preserve_current_bindings 1/1 通过；随后 Rust 单线程全量 302/302 通过（设置项目 Node/ACP fixture）。2026-10-04 新增 executor 端的旧 observation、partial stage、mismatch/unsupported coverage 集成负例，以及 open finding 阻止 finish、原图 SHA 错绑阻止 accepted summary 两个跨 gate 负例；unsupported coverage 汇总丢失的 fail-open 已修复。native_tool_executor 的 review/get-state 集成 fixture 已通过 `complete_preview` 构造匹配当前 revision/fingerprint 的 OCCT host attestation，保留对生产 review/summary binding 的覆盖。mocked provider 响应穿过 normalize→reconcile 并仅为归属当前 scope 的 mismatch 创建 finding。既有 `b921717` partial/full-current-scope=false、`97778c6` 移除 feature 旧 finding 不被 out-of-scope match 关闭仍覆盖。仍需用原图完成真实错位返修/新版复核。见 [范围记录](./32-f7-stage-review-scope.md) 与 [finding 生命周期记录](./40-f7-stage-review-findings.md)。 |
| F7.G5 | P1 | 观察因冲突可重新读取、版本可追踪 | **代码及非法刷新原因拒绝已验收；真实视觉模型行为待验收** | 普通同 SHA/ROI 复用缓存；Agent 显式传 `conflict`/`reobserve` 才重新观察并递增版本；保留历史、SHA/ROI/reason/telemetry；新版 evidence ID 隔离旧 review/finding。新增 `4f70e61` 验证未知字符串和非字符串原因在 provider 请求前被拒；vision domain 15/15，state summary 5/5，tool schemas 3/3。见 [记录](./34-f7-observation-refresh.md)。 |
| F7.G6 | P1 | 统一实际工具与几何能力契约 | **能力映射与构建器边界探针通过；真实 Agent 遵循待验收** | 当前登记 27 类：21 类实际原生 builder、6 类明确不支持。`3b34bcb` 为全部 21 builder 增加有效/越界参数 probes，并为 6 个 schema-valid unsupported kinds 增加拒绝样例；运行时 preflight 和 builder 的边界结果一致。另用真实 OCCT manifest 验证零角回转、超深内六角孔与凹槽深度等于实体厚度三项边界。定向测试 `native_feature_specs` 9/9、`native_plan_geometry` 18/18、`cad_plan_validation` 8/8。真实 DSH Agent 在运行时是否遵循这些能力限制仍待单次多模态冷启动检查。见[原能力契约](./36-f7-native-capability-contract.md)与[验收记录](./63-f7-g6-native-capability-matrix.md)。 |
| F7.G7 | P1 | 分阶段模型、推理预算及行为熔断 | **代码与定向检查通过；Run09 任务级 none 有结构化请求/usage 证据，行为一致性仍失败** | commit `915a252` 在 DeepSeek Responses route 的 source_inventory 任务请求 none，其他任务保留 configured effort；定向测试验证 source_inventory=none、vision_review=low。Run09 三个请求记录 configured=low/requested=none/expected=none、4096 cap，Responses usage reasoning_tokens=0、finish_reason=stop；expected status 为 known_protocol_payload_value，不代表服务端确认。Run08 五次 source_inventory 请求仍为 low/4096，reasoning_tokens=4096 且 length 截断。Run09 source inventory 有效但 verification unknown，建模比例仍不匹配原图。见 [Run08](./50-f7-g8-run-08-diagnosis.md) 与 [Run09](./51-f7-g8-run-09-diagnosis.md)。 |
| F7.G8 | P1 | 真实多模态与多轮链路可复跑验收 | **Run03–14 严格冷启动未通过；Run14 投影显示终态重复待归因；Run15在启动前被执行策略拒绝；随后发现runner原会持久化raw trace/answer/thought长度，已改为默认仅发布安全摘要/allowlist投影（Run15真实重跑待审阅）** | Run12图片链路有效；Run13原图/预览附件链成功，current_geometry、artifacts、holes通过，但acp_cad_tool_results（`acp_cad_tool_result_terminal_update_duplicated`）、visual（coverage/original binding）和finish失败。Run13白名单摘要38条诊断：缺ID gate未出现，isError=true为0、四项error存在标记均为false；只把范围缩到非空ID聚合组，无法判断同状态更新、冲突终态或采集重放。Run14安全投影汇总24个CAD调用组，11组出现重复同类终态更新、1组含failed终态，不能判定重复原因；严格gate保持失败。已实现默认且不可关闭的`acp_terminal_update_projection_v2`和`cordis_binding_projection_v1`安全发布：固定工具名白名单、每工具/状态计数、每工具调用组终态分布、运行期HMAC终态等同/不同聚合，以及ACP→bridge→audit固定原因码计数；不输出ID、摘要、payload或错误正文，不改变门禁逻辑。合成定向测试55/55通过；sentinel fixture确认发布目录只含safe summary、两个allowlist projection与build状态，不泄露ACP/native ID、answer/thought/payload。runner运行态raw records仅在内存/临时工作区用于gate并在正常退出清理。Run15确认原图SHA、固定Node/DSH、DeepSeek `deepseek-flash` / low配置及OCCT manifest可用，但CLI启动动作被执行器拒绝（`blocked by policy`），故没有provider请求、gate结果或v2真实运行证据。详见[Run15 preflight](./60-f7-g8-run-15-preflight.md)。2026-10-04 静态审计发现有界超时后仅发送终止信号便进入scratch清理的竞态；现已增加第二个退出等待期限、顶层异常处理等待托管子进程退出，以及固定枚举`summary.json.scratch_cleanup_status`。Node定向套件77/77通过（含3个fake-child/清理故障场景及“严格门禁通过、清理失败”归档回归）；summary.json总体状态现仅在严格门禁通过且清理成功时为passed，严格门禁状态保留为独立字段；控制台与归档报告一致。没有重跑真实Run15，清理仍是有界尽力操作，若子进程未退出或删除失败会记录failed并以非零状态结束。2026-10-04 `e9b9f2a` 增加 gate 固定失败原因码的白名单投影，未知原因不写出；同一 77 项 Node 套件仍为 77/77，sentinel 过滤回归覆盖。2026-10-04 增加启动时过期 scratch 保守清扫：owner marker schema/PID/时间/随机 nonce 校验，限定系统 tmpdir 直接子目录和专属前缀，拒绝 symlink/reparse，PID 仅 `ESRCH` 判死；清扫结果以固定枚举和计数进入安全 summary。定向 Node 套件83/83通过，覆盖死/活/未知 PID、年龄阈值、marker/路径拒绝及删除失败不泄露；该机制只在后续启动时清理可确认的过期目录，不是可靠擦除保证。详见[runner 生命周期记录](./61-f7-g8-runner-cleanup-lifecycle.md)。下一步仍需在允许启动本地严格runner的上下文中单次冷启动，并仅审阅脱敏projection和自动gate；不得读取ID、payload、原始trace/thought或错误正文，也不得放宽门禁。visual原图binding/coverage、finish、真实返修、多图/纯文字/多轮/取消及WebView/CATIA分层验收仍缺；此代码改进不代表真实视觉验收通过。见[Run12](./55-f7-g8-run-12-diagnosis.md)、[Run13](./56-f7-g8-run-13-diagnosis.md)、[Run14](./59-f7-g8-run-14-diagnosis.md)、[Run15 preflight](./60-f7-g8-run-15-preflight.md)、[终态契约审计](./56-f7-g8-terminal-update-contract-audit.md)与[visual/finish evidence audit](./58-f7-visual-finish-evidence-audit.md)。 |
执行顺序：继续完成F6.5–F6.7及F4/F5未完成项，同时优先补齐F7 P0 G1–G4缺口。Run13已确认非空ID组出现多次terminal update；ACP契约审计没有通知序号或at-most-once保证。已增加安全事件投影，下一次单次隔离冷启动启用它以获取状态类别、顺序和次数分布；不凭有限摘要放宽门禁。G2/G3真实WebView IPC、visual binding/coverage、finish、实际返修、多输入模式、多轮与取消仍待验收；visual 与 finish 的生产字段来源、排障顺序及 headless 测试缺口见[专项审计](./58-f7-visual-finish-evidence-audit.md)。每次协议/schema/prompt变更都需新隔离冷启动复验。F7只有所有链路整改和真实验收完成后才能标记完成。

## F0 验收记录

- [x] 迁移前工作树干净；记录 tracked 1,654 文件基线。
- [x] 把桌面端归入 `src/apps/desktop/`，Python 过渡工程归入 `src/legacy/`。
- [x] 把旧 `docs/`、`reports/`、`artifacts/` 保真归档到 `temp/`，没有删除历史文件。
- [x] 建立 `docs/README.md` 唯一根文档索引、编号目录、研究章程、架构基线、验证协议和前端设计规范。
- [x] 新增 `temp_path()`，Agent、selftest、CADPlan 和截图输出改到 `temp/`。
- [x] 改好遗留启动脚本、Tauri 资源 staging、开发根路径和 UI 手册路径。
- [x] 更新 Actions、贡献文档和内部路径引用，并在 CI 加入目录/运行路径回归测试。
- [x] Python selftest 36/36；Python pytest 277 passed、92 subtests passed；Vue build 通过；Rust tests 4 passed；资源 staging 通过；`git diff --check` 无错误。
- [x] 对比移动前后历史文件数量和 Git blob：迁移阶段索引保留 1,654 个原有 blob，内容多重集与迁移前 HEAD 一致；后续仅对列明的路径/测试适配文件作了修改；`.github/` 根工作流保留，本地缓存忽略规则正常。
- [x] 检查 docs 只有一个根级 Markdown；仓库根目录只留 `docs/`、`src/`、`temp/`、必要的 `.github/` 与仓库元数据/说明文件。
- [x] F0 整体验收完成，形成一个 `refactor: reorganize project into src docs and temp` 提交到 `main`。

旧协作包打包器仍含历史开发机绝对路径和旧包布局假设，不属于当前 Tauri 启动/打包链路；后续若继续使用该协作包工作流，需独立迁移并验收。

## 后续 feature 的最小验收卡

每项开工前写明输入、成功标准、失败样本和可复现命令；结束前记录代码变动、运行证据、风险和剩余项。Agent 相关 feature 必须留一次真实 DSH 请求的版本/会话/图像输入/工具事件记录；几何 feature 必须留可机器读取的 B-Rep 与尺寸报告；安装 feature 必须留无全局依赖的冷启动结果。

完成后由主 Agent 检查对照标准和未解决项，再以独立 commit 提交。静态测试通过不代表真实 DSH 多模态或 CATIA 验收通过。

F1 拆分与实施记录见[ACP Host 子 feature 卡](./02-f1-acp-host.md)。
F2.1 的本机 Cordis 工具桥边界与验收记录见[原生 Cordis → Rust 工具桥](./04-f2-cordis-bridge.md)。
F2.2 的 Rust schema 来源与真实 DSH schema 编译验收记录见[Rust 持有 Cordis 工具 schema](./05-f2-rust-tool-schemas.md)。

F2.3 的原生读取工具、真实 DSH 轨迹与联网边界见[Rust 原生状态、知识与联网工具](./06-f2-native-read-tools.md)。
F3.1 的事务语义、浮点解析与对拍记录见[Rust CADPlan 增量事务](./07-f3-plan-transactions.md)。

F3.2 的参数校验、静态支持边界和双接口对拍记录见[Rust CADPlan Schema 与静态校验](./08-f3-plan-validation.md)。

F2.4.1 的真实图片传输、解析器缺陷和观察质量边界见[Rust 多模态请求与图片输入基础](./09-f2-native-vision-transport.md)。

F2.4.2 的原生视觉工具接线、单视图复核数据隔离及质量验收边界见[原生视觉工具接线](./15-f2-native-vision-domain.md)。

F2.5 的原生 CAD 写工具、真实 DSH→Cordis→Rust→OCCT 调用及 Python worker 移除见[Rust 原生 CAD 写工具](./21-f2-native-cad-write-tools.md)。

F5.4 的原生活动模型 Wavefront OBJ 导出见[Rust 原生 OBJ 导出](./40-f5-native-obj-export.md)。
F7.G3 的状态真值与当前模型验收展示见[可见状态记录](./42-f7-g3-visible-acceptance-state.md)。Run03–07 均为严格冷启动失败样本；Run07 只通过了视图枚举修正的行为检查，不能据此推断 F7/G8 通过。

F4.1 的真实 SDK、DLL 生命周期缺陷和几何检查记录见[固定 OCCT SDK 与 Rust 几何基础](./10-f4-native-occt-foundation.md)。

F4.2.1 的坐标契约修复、实际参数对拍和测量边界见[CADPlan 基础特征原生执行](./11-f4-basic-plan-execution.md)。
