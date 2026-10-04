# Rust + DSH 重构总 Todo

更新时间：2026-10-04。每一项 feature 按边界实现、验收并单独提交。首个“目录重构”feature 完成后直接提交到 `main`；后续 feature 使用独立提交，不把未验收工作合并。

## 总目标

桌面程序启动整套建模流程：Rust/Tauri 管理应用、runtime 与 CAD 状态；DSH Agent 自主决定是否以及如何调用 DSH 原生 Cordis 工具；Rust 负责 CADPlan 与几何核心；Python 最终只保留 CATIA COM。保留快速模式，并允许快速模式与 Agent 创造模式切换。

## 当前进度

| ID | Feature | 状态 | 完成条件 / 提交边界 |
|---|---|---|---|
| F0 | 目录重构、文档重建和路径恢复 | **进行中（目录迁移、Vite headless、`npm run dev` CLI 与隐藏 WebView CLI 验收通过；Tauri `devUrl`/Vite IPv4 地址契约已修正；可见窗口和原报告的无界面现象仍待验证）** | 根目录 npm run dev 启动 Tauri，npm run dev:web 启动 Vite；桌面包目录 npm run dev 是 Vite，npm run tauri:dev 启动 Tauri。2026-10-04 合并前 main 的浏览器快照出现后端未连接和 favicon 404；集成分支 Vite headless 显示浏览器预览已就绪、桌面操作禁用、Console 0 error/0 warning。隐藏窗口 CLI WebView2 harness 四场景通过，确认 smoke production bundle 首屏 mount 和 Tauri Channel→Vue handler 事件投递。隔离 F0 worktree 实际执行桌面包 `npm run dev`，Vite 监听 `127.0.0.1:1420`，首页和入口模块均 HTTP 200，Ctrl-C 后端口释放；这些 CLI 检查不验证浏览器渲染、可见 Tauri/WebView2 或原生 IPC。Tauri devUrl 固定为 `127.0.0.1:1420` 匹配 Vite IPv4 bind，并有 Node 配置回归锁定。详见 [Tauri dev 与 Vite 启动核查](../05-operations/03-tauri-dev-cli-preflight.md)、[F0 Vite CLI 实测](./72-f0-vite-cli-verification.md) 与 [WebView harness](./62-f7-webview-driver-harness.md)。 |
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
| F4 | Rust/C++ thin OCCT FFI | **进行中** | 固定官方 OCCT 7.9.3 SDK、C ABI/Rust 包装及本机 packaged probe 已通过；原生 registry 覆盖 27 类规格中的 24 类，未实现 `loft`、`impeller`、`shell`；已实现类仍有完整拒绝变体及最终安装包/干净机验收缺口；含 scroll_disc 的本机 Rust/Python OCP 矩阵 89 正例、54 负例通过 |
| F4.1 | 固定 OCCT SDK、C ABI 与 Rust 几何基础 | **已完成** | 实际 Rust → C ABI → OCCT fixture 通过，正常退出；27 类 CADPlan feature registry 中有 22 类已接入原生执行器，安装交付仍待完成 |
| F4.2 | CADPlan 基础特征到 OCCT 的原生执行 | **进行中** | 已接入 box/cylinder/sphere/hole/pocket、cone/prism/revolve、gear/helical_gear/bevel_gear/socket_head_bolt、pattern、piston、camshaft、crankshaft、timing_pulley、vbelt_pulley、scroll_disc、helix、worm 等特征；其余 `loft`、`impeller`、`shell` 特征映射与安装 runtime 仍未完成 |
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
| F4.2.16 | CADPlan scroll_disc 原生执行 | **已完成（标记近似）** | Rust OCCT 采样阿基米德螺旋棱柱与圆柱背盘融合；全局 Z 轴、center 几何中点、mm 归一化、有效单闭合体；旧 B-Rep 正例 bbox 差 0 mm、volume 差 ≤`9.1e-12 mm³`，OCCT tessellated area 估算最大相对差 `0.10913%`（边界 `0.6%`）；旧 oracle、平移与 cm 正例共 3 个，退化/资源/自交/不相交融合拒绝 7 个；CATIA 仍省略螺旋壁。详见[验收记录](./64-f4-native-scroll-disc.md) |
| F4.2.17 | CADPlan helix 原生执行 | **已完成（标记近似）** | 接入 Rust native builder、单位归一化、preflight 与 capability；4 个轴向/平移/taper/cm Rust→OCCT 正例均生成单一有效闭合 facet-BRep，与旧 Python OCP 包围盒/体积误差均为 0（容差分别 `1e-6 mm` / `1e-5 mm³`）；3 类 schema-valid 负例在 OCCT 前拒绝。完整共享 parity 矩阵未完成；CATIA mapping 仍 unsupported。详见[验收记录](./65-f4-native-helix.md) |
| F4.2.18 | CADPlan worm 原生执行 | **已完成（标记近似）** | Rust 用 48 边棱柱轴身 + native helix facet-BRep 实际 fuse；3 个旧 Document/OCP 对拍（标准切触、平移、cm）BBox 最大差 0 mm、体积误差 ≤`6.19e-11 mm³`；5 类负例在建模前拒绝。另以 8 边轴身验证实际 OCCT fuse 返回 `valid=1, solid_count=2, closed=2` 时 builder 拒绝候选，不冒报成功。结果强制单有效闭合体；仅全局 Z 同轴。wire/helix 和轴身多边形仍为近似 facet-BRep；CATIA 仍只导出光轴。详见[验收记录](./66-f4-native-worm.md) |
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
| F7 | 多模态盲测与科学验收基线 | **进行中（Run18严格冷启动失败；Run19未能取得完整安全发布证据，F7保持失败）** | Run18 在固定 `9a2f5b04a5c90d29a36283dda89f4c852c3d2bdf` 上真实运行且 strict 失败，详见[Run18](./70-f7-g8-run-18-diagnosis.md)。Run19 在固定 `542ca3fc3ca172bcf7d6af6feabf68a91d2d3e3e` 上三份白名单中仅 `summary.json` 存在；gate map/reason codes、ACP/Cordis projection、build status 与运行原图 SHA 未发布，不能确认是否发出 provider 请求或 schema 修复效果，详见[Run19尝试](./71-f7-g8-run-19-diagnosis.md)。F7 仍失败，不得标记通过。 |

Coldstart runner 已采用默认且唯一发布 safe gate summary、ACP/Cordis allowlist projection、固定 enums/reason counts 和有界 scratch 清理；相关合成测试及安全 sentinel 回归已通过。Run16 实际结果记录在[独立诊断](./67-f7-g8-run-16-diagnosis.md)，本次只允许审阅白名单摘要，不能从原因码推断具体模型或几何错误。精确 ACP↔Cordis 调用ID绑定已由 9ea84df 修复；Run17需用该提交的新二进制确认真实 DSH行为。

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

### F7/G8 最新进度（2026-10-04）

- Run19 在固定 `542ca3fc3ca172bcf7d6af6feabf68a91d2d3e3e` 的独立 detached worktree 上只启动一次，exit code 1、耗时 125.6 秒。运行前原图 SHA/字节数、pinned Node/DSH 与 keyless 公开 DeepSeek descriptor 均核验；但输出只有 `summary.json`，其状态为 failed、scope_unverified、transport failed、model none、failure_reason_count 1、evidence_error_count 0、runtime unverified、original_image null、scratch cleanup removed。gate map/reason codes、ACP/Cordis projection 与 build status 缺失；不能确认 provider request、运行时原图绑定或根因。只记录白名单 summary SHA；细节见[Run19尝试诊断](./71-f7-g8-run-19-diagnosis.md)。该尝试不能代表完整真实模型验收，F7 保持失败。

- Run15 使用默认参考图完成了一次真实 DSH 冷启动，provider transport、原图发送、几何产物和本地构建通过；strict gate 未通过：CAD 工具身份/结果关联、孔测量、视觉覆盖与原图绑定、finish 均失败。只审阅安全摘要和固定 projection；未查看 raw trace、模型 answer/thought、工具 payload/ID、GUI 或 CATIA。完整原因码见 [Run15 记录](./60-f7-g8-run-15-preflight.md)。
- Run16 在 9ea84df 之前构建，使用同一默认原图（72,309 bytes，SHA-256 53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553）和隔离 DSH home。单次 CLI 到 900 秒边界，overall/transport/DSH 与 Cordis/ACP、current geometry、holes、visual、finish gate 失败；run scope、trace integrity、原图、主图 wire、artifacts 通过，scratch 清理为 removed。只检查 safe summary/projections/build status，详细原因码和限制见[Run16诊断](./67-f7-g8-run-16-diagnosis.md)；不能据此归因具体模型回答或几何特征。
- 9ea84df 将锁定的 DSH RC2 exec.callId 作为临时 acp_call_id 传递并精确配对，保留原 native UUID 用于 Rust audit/取消/幂等；Node gate 89/89、RPC 6/6通过，覆盖同参重叠调用、ID缺失/错配和safe projection不泄漏。真实 DSH 是否按预期提供并使用该关联，待 Run17 新二进制复验；细节见[调用身份绑定验收](./68-f7-g8-cordis-acp-callid-binding.md)。
- `2eabd1e` 曾修复重复同态 ACP 快照被误判为多次执行；随后 `9ea84df` 加入精确 callId 配对时又把终态通知数限定为 1，因此 Run16/Run17 使用的门禁仍会误拒重复 upsert。`e8f02ce` 已恢复按唯一终态状态判断，完整保留调用 ID、参数、作用域和失败状态门槛；Run17 仍需按新代码做真实冷启动。
- `2b2cf96` 将视觉复核绑定改为 fail closed：review 必须带清单 SHA 和自身 observation ID，并匹配当前完整原图 inventory 的 SHA/observation ID；6/6 定向 Rust 测试通过，覆盖有效绑定及 review/inventory 关联缺失。它修正错误归因/潜在错误覆盖边界，不代表模型视觉理解或 Run15 原图覆盖已通过；见[视觉来源绑定修复](./66-f7-visual-reference-binding.md)。
- `2461295` 为 runner 增加对后续启动时过期 private scratch 的保守回收，83/83；`fd52e62` 记录 Run15 安全真实复跑。F4 `scroll_disc` 原生近似构造已由 `e492aab` 单独提交：89 正例、54 负例对拍通过，CATIA 仍不支持其螺旋壁。
- Run17 单次真实冷启动以非零结果结束：8 个 gate 通过，4 个 gate 失败，细节见 [Run17诊断](./69-f7-g8-run-17-diagnosis.md)。安全聚合显示 7 组重复同态终态更新和 7 次 ACP→bridge 歧义，但不含逐调用关联，不能声称二者一一对应。当前 gate 因 `terminal_updates !== 1` 拒绝重复 upsert；静态审查与既有 ACP 状态契约确认这是过严计数，`e8f02ce` 已移除该条件并保留唯一终态状态与精确调用关联要求，89/89 回归通过。`cad_review_reference_similarity` 有一次 bridge 执行被拒绝，尚待源代码层面归因。
- Run18 在固定 HEAD `9a2f5b04a5c90d29a36283dda89f4c852c3d2bdf` 上完成一次真实 DSH 冷启动，exit code 1、耗时 787.8 秒；原图 SHA 与既有 Run 相同。DSH、main image wire、current geometry、holes、artifacts gate 通过；Cordis/ACP 工具与结果、visual、finish gate 失败。ACP projection 记录同态 pair 0、missing call ID 0；Cordis projection 有 4 个 `bridge_execution_rejected` 和一个 `cad_update_feature` `bridge_call_missing`。摘要没有逐调用参数，不能据此归因 request args；详细安全结果见[Run18诊断](./70-f7-g8-run-18-diagnosis.md)。`cad_review_reference_similarity` 未列于本轮拒绝枚举，但 visual gate 仍失败，不宣称视觉复核通过。
- F0 隐藏 WebView CLI harness 已通过四场景，production Vue 首屏挂载与 Channel 事件投递正常；它不验证可见窗口。随后在隔离 F0 worktree 实际执行桌面包 `npm run dev`：Vite 8.3.2 在 1103 ms 就绪并监听 `127.0.0.1:1420`，`/` 与 `/src/main.ts` 均返回 HTTP 200，Ctrl-C 后监听端口释放。此项只验证 Vite CLI 服务与资源响应，不验证浏览器像素/交互、Tauri 窗口、WebView2 或原生 IPC，因此仍不能声称用户报告的“完全没有界面”已由运行时复现或修复。见[F0 Vite CLI 实测](./72-f0-vite-cli-verification.md)与[WebView harness](./62-f7-webview-driver-harness.md)。
- F0 的 `devUrl`/Vite IPv4 对齐已通过独立提交 `52757c3` 集成到当前分支；配置回归、前端 build 和 Vite CLI HTTP 检查通过，实际 Tauri dev/WebView 可见窗口仍未启动；Run18 不覆盖该问题。
- 所有累计提交仍仅在本机 `feat/f7-p0-completion`，尚未合并或推送到 `origin/main`。Run18 只确认继承环境中的 provider key 存在，无法验证与 Run17 的 key 字节相同。下一步先按 Run18 projection 静态审查 producer→consumer 工具参数/身份路径，特别是 `cad_update_feature` schema 与 bridge 缺失、四类 bridge 执行拒绝；不读取 raw trace 或工具 payload。完成有证据支持的代码修复后，再固定新 HEAD、同一原图和隔离输出重跑真实 DSH；不降低视觉/finish 门槛。Run18 失败细节见[Run18诊断](./70-f7-g8-run-18-diagnosis.md)。
| ID | 优先级 | 任务 | 状态 | 完成条件 |
|---|---|---|---|---|
| F7.G1 | P0 | 主 Agent 直接查看原图和当前单视图 | **原图/单视图附件传输及 ACP 多图构造回归通过；真实视觉理解/验收仍失败** | Run12 原图 SHA `53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553`；`cad_get_reference_image` 的 10/10 Cordis result 为 `isError=false`，`image/png` 经 native decode 和 `attachment_save` 均成功；`cad_preview_model` 2/2 同样成功，且 `main_image_wire` passed。`082097b` 新增实际生产 helper 测试，核对多张 ACP image part 的顺序、MIME、Base64 解码字节与 active-turn manifest SHA 一一对应；1 passed、291 filtered。独立视觉 review mock 也逐字节断言原图+当前单视图预览传输。上述仅证明 ACP/视觉工具载荷与 provenance 接线，不证明真实 DSH 收到图后理解正确。Run09 verification 仍 unknown；`visual` gate 仍报 `visual_coverage_incomplete_or_conflicting`、`visual_gate_original_reference_binding_missing`，视觉验收不能标通过。Headless predicate 翻转回归覆盖 visual coverage 6 项、原图 binding 6 项、host finish 7 项；native_tool_executor 集成负例现覆盖 source SHA 错绑、错误 reference ID、仅 ROI 库存、空/重复 evidence、旧 observation 刷新、partial stage、mismatch 与 unsupported coverage。测试发现并修复 unsupported_coverage_ids 在 cad_get_state 汇总中丢失、导致错误提升为完整 coverage 的 fail-open；现由 executor→cad_get_state 回归锁定。设置项目 IPC 测试所需 Node/ACP fixture 后，全量 Rust 单测 302/302 通过。真实单/多图视觉理解与正确复核仍待真实 DSH/provider 原图验收；见 [executor 库存绑定负例](./65-f7-g1-executor-inventory-binding.md) 与[Run09](./51-f7-g8-run-09-diagnosis.md)、[Run12](./55-f7-g8-run-12-diagnosis.md)、[ACP多图构造回归](./64-f7-g1-acp-image-prompt-regression.md)与[visual/finish evidence audit](./58-f7-visual-finish-evidence-audit.md)。 |
| F7.G2 | P0 | CADPlan、Agent 与桌面模型统一同步 | **隐藏 WebView2 → Rust Channel → 生产 Vue handler 四场景 CLI 验收通过；桌面渲染与真实 DSH 同步仍待验收** | Agent/Quick/特征编辑、活动模型恢复、Save As、STL/OBJ/report/多视图快照和显式旧工程转换都路由到活动模型；Agent 几何工具通过 generation CAS 防止旧会话覆盖。a4dfe2b 验证 Agent 编辑发布共享 generation 并同步会话快照；a2cc562 修复 Vue reactive settings 序列化问题。`c60e92e` 覆盖 Host→fake ACP→本地事件 sink；`8cbdaab`、`3b3f119` 增加 `npm run test:agent-channel-vue`，由 Tauri MockRuntime 调用共享 `agent_send` 核心、捕获 Rust `Channel<Value>` JSON，再交给生产 Vue Channel callback；验证 model revision/document/feature、request id 和 terminal candidate。`1b13d1e` 新增 smoke-only Tauri binary 和 CLI WebDriver harness；首次隐藏 WebView 运行中纯问答、工具失败通过，accepted-current 因 fixture 漏 `acceptance_confirmed` 超时；`03080f1` 修复 fixture 后用匹配 Runtime 的 EdgeDriver 和本地 `tauri-driver` 唯一复跑 4/4 场景通过。该 smoke 只证明 production bundle 的 WebView→Channel→Vue 投递，不等于真实 DSH ACP、桌面布局/像素/WebGL 或用户体验验收。启停与 CATIA 原生导出仍明确受限。见[活动模型同步记录](./38-f7-active-model-sync.md)与[WebView harness 说明](./62-f7-webview-driver-harness.md)。 |
| F7.G3 | P0 | 请求结束与模型验收状态区分 | **Host finish 新鲜度与接受条件 headless 翻转矩阵、Vue 矩阵及真实隐藏 WebView 四场景通过；真实 DSH 运行态仍待验收** | `1dd79b5` 的四场景 Channel→Vue 矩阵覆盖纯问答、工具失败、当前 accepted、旧 revision stale accepted；`1b13d1e` 增加隔离 WebView2 CLI harness；首次 accepted-current fixture 漏字段失败，`03080f1` 修复后真实隐藏 WebView2 四场景 4/4 通过。`26e3362` 将 finish acceptance 绑定到 canonical evidence snapshot：same revision 下视觉库存/复核、原图 manifest、finding、外部事实或几何检查变化即失效，证据不变仍 accepted；Vue 只接受 host `acceptance_confirmed=true`。本次新增 host session 负例覆盖 local geometry、reference coverage、construction milestone、required external facts 四项；既有用例覆盖整体 geometry review、孔测量和开放 finding，逐项阻断时保持 `accepted=false`。另补两条跨 gate 独立性负例：视觉全覆盖但 open finding 阻断 finish；finish summary 自称 accepted 但原图 reference row SHA 错绑时 host summary 回落为 candidate。经仓库 Windows test wrapper `src/apps/desktop/src-tauri/scripts/test-lib.ps1 -Offline`（设置项目 IPC 所需 Node/ACP fixture）全量复跑，302/302 通过，包含上述两条负例。先前绕过 wrapper 直接启动测试 exe 曾报 `STATUS_ENTRYPOINT_NOT_FOUND`；wrapper 为测试进程提供 Common Controls v6 sidecar manifest。mock ACP/headless 不等于真实 DSH/WebView/视觉质量；用户可见状态及真实多模态运行仍待验收。见[状态真值](./29-f7-turn-and-model-status.md)、[可见状态记录](./42-f7-g3-visible-acceptance-state.md)、[finish证据绑定记录](./51-f7-finish-evidence-binding.md)、[visual/finish evidence audit](./58-f7-visual-finish-evidence-audit.md)与[WebView harness说明](./62-f7-webview-driver-harness.md)。 |
| F7.G4 | P0 | 阶段复核与施工范围一致 | **宿主成功构建绑定、production normalize→reconcile 回归与移除特征负样例通过；真实视觉返修待验收** | `verified_stage_scope` 现在要求当前 revision/plan fingerprint 对应的 `last_preview` 为 passed，并验证 `native_cad_session.complete_preview` 的报告 SHA attestation；Agent milestone 自报不能独立证明 feature 已施工。enabled hole 自报 completed、但无成功构建 attestation 时 provider 前拒绝；native_vision_domain 19/19、native_review_findings 5/5、native_executor_inspect_current_review_and_get_state_preserve_current_bindings 1/1 通过；随后 Rust 单线程全量 302/302 通过（设置项目 Node/ACP fixture）。2026-10-04 新增 executor 端的旧 observation、partial stage、mismatch/unsupported coverage 集成负例，以及 open finding 阻止 finish、原图 SHA 错绑阻止 accepted summary 两个跨 gate 负例；unsupported coverage 汇总丢失的 fail-open 已修复。native_tool_executor 的 review/get-state 集成 fixture 已通过 `complete_preview` 构造匹配当前 revision/fingerprint 的 OCCT host attestation，保留对生产 review/summary binding 的覆盖。mocked provider 响应穿过 normalize→reconcile 并仅为归属当前 scope 的 mismatch 创建 finding。既有 `b921717` partial/full-current-scope=false、`97778c6` 移除 feature 旧 finding 不被 out-of-scope match 关闭仍覆盖。仍需用原图完成真实错位返修/新版复核。见 [范围记录](./32-f7-stage-review-scope.md) 与 [finding 生命周期记录](./40-f7-stage-review-findings.md)。 |
| F7.G5 | P1 | 观察因冲突可重新读取、版本可追踪 | **代码及非法刷新原因拒绝已验收；真实视觉模型行为待验收** | 普通同 SHA/ROI 复用缓存；Agent 显式传 `conflict`/`reobserve` 才重新观察并递增版本；保留历史、SHA/ROI/reason/telemetry；新版 evidence ID 隔离旧 review/finding。新增 `4f70e61` 验证未知字符串和非字符串原因在 provider 请求前被拒；vision domain 15/15，state summary 5/5，tool schemas 3/3。见 [记录](./34-f7-observation-refresh.md)。 |
| F7.G6 | P1 | 统一实际工具与几何能力契约 | **能力映射、构建器边界与 scroll_disc B-Rep parity 已验收；真实 Agent 遵循待验收** | 当前登记 27 类：22 类实际原生 builder、5 类明确不支持。scroll_disc boundary probes 及集成 OCP/Rust 矩阵 89 正例、54 负例通过。真实 DSH Agent 在运行时是否遵循这些能力限制仍待单次多模态冷启动检查。见[原能力契约](./36-f7-native-capability-contract.md)、[能力矩阵](./63-f7-g6-native-capability-matrix.md)与[scroll_disc 验收记录](./64-f4-native-scroll-disc.md)。 |
| F7.G7 | P1 | 分阶段模型、推理预算及行为熔断 | **代码与定向检查通过；Run09 任务级 none 有结构化请求/usage 证据，行为一致性仍失败** | commit `915a252` 在 DeepSeek Responses route 的 source_inventory 任务请求 none，其他任务保留 configured effort；定向测试验证 source_inventory=none、vision_review=low。Run09 三个请求记录 configured=low/requested=none/expected=none、4096 cap，Responses usage reasoning_tokens=0、finish_reason=stop；expected status 为 known_protocol_payload_value，不代表服务端确认。Run08 五次 source_inventory 请求仍为 low/4096，reasoning_tokens=4096 且 length 截断。Run09 source inventory 有效但 verification unknown，建模比例仍不匹配原图。见 [Run08](./50-f7-g8-run-08-diagnosis.md) 与 [Run09](./51-f7-g8-run-09-diagnosis.md)。 |
| F7.G8 | P1 | 真实多模态与多轮链路可复跑验收 | **Run19未取得完整安全发布证据；Run18仍是最近一次具有完整安全 projection 的真实运行且 strict 失败** | Run19 使用固定 detached HEAD `542ca3fc3ca172bcf7d6af6feabf68a91d2d3e3e`、默认原图与 prompt、无预制 CADPlan/尺寸/孔数，只启动一次。三份白名单中仅 `summary.json` 存在，`tool-result-diagnostics.jsonl` 与 `build-status.json` 缺失；scope、gate map、reason codes、运行原图 SHA 未发布。不能确认是否发出 provider 请求、构建结果、schema 修复效果或失败根因。详细安全边界见[Run19诊断](./71-f7-g8-run-19-diagnosis.md)。F7 保持失败，不得标记通过。 |
| F7.G9 | P0 | 原生 Cordis CAD 工具契约修复 | **已验收并独立提交 `542ca3f`** | 动态 map 通过 pinned Cordis defineTool；Rust 复用 CADPlan validator；create/remove/restore/inspect 合同锁定；Run18 参数未知，不据静态检查归因。静态/fixture 验收不等同真实 DSH 冷启动通过。 |
执行顺序：先静态检查 Run19 runner 启动与安全 artifact publication 路径，解释 summary 存在但 projection/build status 缺失的代码路径；未由代码/安全证据证实的原因保持 unknown。若后续需要真实 provider 验收，应在明确授权后另用固定 commit、全新 detached worktree 和输出目录运行一次；始终仅审阅 allowlist artifacts。随后继续 F4 缺失 builder、F6 干净账户冷启动/CATIA 层验收及 F7 P0/P1；协议/schema/prompt 修改须另起隔离冷启动。F7 在全部链路整改和真实验收完成前不得标记通过。

## F0 验收记录

- [x] 2026-10-04：修复 Tauri devUrl `localhost` 与 Vite `127.0.0.1:1420` 的地址契约不一致；新增 Node 配置回归，修改前失败、修改后 4/4 通过；`npm run build` 通过，只有 637.32 kB `CadViewport` chunk 大小提示。CSP 为 `null`、capability 不含按 host 授权项，无需同步 host allowlist。此项没有运行 `npm run dev`、Tauri dev 或可见 WebView。
- [x] 2026-10-04：隔离 F0 worktree 运行桌面包 `npm.cmd run dev`，Vite 8.3.2 于 1103 ms 就绪，监听 `127.0.0.1:1420`；`GET /` 返回 200 HTML，`GET /src/main.ts` 返回 200 JS；Ctrl-C 后确认监听端口释放。该证据不涵盖浏览器渲染、Tauri/WebView2 窗口或 IPC，用户报告的无界面表现仍未复现。详见[CLI 实测记录](./72-f0-vite-cli-verification.md)。

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
