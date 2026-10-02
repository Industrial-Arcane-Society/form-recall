# Rust + DSH 重构总 Todo

更新时间：2026-10-03。每一项 feature 按边界实现、验收并单独提交。首个“目录重构”feature 完成后直接提交到 `main`；后续 feature 使用独立提交，不把未验收工作合并。

## 总目标

桌面程序启动整套建模流程：Rust/Tauri 管理应用、runtime 与 CAD 状态；DSH Agent 自主决定是否以及如何调用 DSH 原生 Cordis 工具；Rust 负责 CADPlan 与几何核心；Python 最终只保留 CATIA COM。保留快速模式，并允许快速模式与 Agent 创造模式切换。

## 当前进度

| ID | Feature | 状态 | 完成条件 / 提交边界 |
|---|---|---|---|
| F0 | 目录重构、文档重建和路径恢复 | **已完成** | 根目录结构、旧资料归档、文档索引、日志路径、开发启动、测试、Tauri build hook 和 GitHub Actions 已核验；本提交整体落到 `main` |
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
| F4 | Rust/C++ thin OCCT FFI | **进行中** | 固定官方 OCCT 7.9.3 SDK、C ABI/Rust 包装及本机 packaged probe 已通过；执行 registry 当前覆盖 27 类规格中的 14 类，另 13 类及已实现类的拒绝变体、最终安装包/干净机验收仍未完成 |
| F4.1 | 固定 OCCT SDK、C ABI 与 Rust 几何基础 | **已完成** | 实际 Rust → C ABI → OCCT fixture 通过，正常退出；原生 27 类 CADPlan 执行及安装交付仍待完成 |
| F4.2 | CADPlan 基础特征到 OCCT 的原生执行 | **进行中** | 已接入 box/cylinder/sphere/hole/pocket、cone/prism/revolve、gear/socket_head_bolt、pattern、piston 等特征；其余特征映射与安装 runtime 仍未完成 |
| F4.2.1 | 五类基础特征与参数坐标对拍 | **已完成** | box/cylinder/sphere/hole/pocket 的基础变体已执行；24 组真实几何对拍、4 个负样本通过，孔记录未冒充独立测量 |
| F4.2.2 | 圆锥、棱柱和回转能力 | **已完成** | 三类原生构造器及单位/坐标接线完成；含负角回转的 44 组旧内核几何对拍通过 |
| F4.2.3 | 齿轮与螺钉原生几何执行 | **已完成（标记近似）** | 完整 CADPlan 接入 gear/socket_head_bolt；无尺寸时拒绝兜底；几何包围盒/体积对拍，螺纹近似 warning |
| F4.2.4 | CADPlan 线性与圆周 pattern 原生执行 | **已完成** | 复制紧邻来源特征的原生 B-Rep 刀具；全局 XYZ/右手圆周变换，孔轴方向与孔位同步变换；Rust/Python OCP 54 正例、9 负例对拍；证据见 [F4.2.4 验收记录](./22-f4-native-pattern-execution.md) |
| F4.2.5 | CADPlan piston 原生几何执行 | **已完成** | 保留旧活塞剖面、环槽和 X 轴销孔语义；Rust/Python OCP 57 正例、12 负例全量对拍通过（本项新增 3 正例、3 负例）；CATIA 映射仍为 approximate，证据见 [F4.2.5 验收记录](./23-f4-native-piston-execution.md) |
| F4.2.6 | CADPlan spoked_wheel 原生几何执行 | **已完成** | 全局 Z 轴轮缘、带孔轮毂、辐条及 cm 单位换算；真实几何全量 60 正例/15 负例通过；见 [F4.2.6 记录](./24-f4-native-spoked-wheel.md) |
| F4.2.7 | CADPlan conrod 原生执行与平移修复 | **已完成（标记近似）** | 修复旧Python杆身重复平移，Rust环形头+梯形杆身；63正例/18负例对拍，平移不变性与cm换算；见 [记录](./31-f4-native-conrod.md) |
| F4.2.8 | CADPlan cross_joint 原生执行 | **已完成（标记近似）** | OCCT 中心块与四轴颈解析融合；有效单闭合体及轴心材料区间检查；66正例/20负例；与旧24段网格体积差0.95857%，不作精确体积对拍；见 [记录](./33-f4-native-cross-joint.md) |
| F4.3.1 | B-Rep 材料区间与离散孔证据基础 | **已完成** | 精确面交事件、Rust RAII、41 射线 pre/post 对比；真实九孔/盲孔/错位/错径正负样例通过，非孔径计量，生产接线待完成 |
| F4.3.2 | 孔材料采样接入 CADPlan | **已完成** | 44 对拍、6 负样例、局部材料盲孔与近边孔部分证据通过；采样不冒充孔径计量 |
| F4.3.3 | 最终 B-Rep 孔计量接入 CADPlan | **已完成** | 最终形体实测孔半径、轴线与 360° 覆盖；盲孔/贯穿孔核验，后续特征会使旧测量失效 |
| F4.4.1 | 精简 OCCT 资源组装与定位 | **已完成** | 17 DLL 哈希校验打包，Node 2/2、Rust 7/7；系统 PATH 下实际 packaged probe 通过，最终安装包/干净机器仍待验收；见 [记录](./26-f4-packaged-occt-runtime.md) |
| F5 | Quick 模式与本地导出迁移 | **进行中** | Rust Quick、活动模型路由、AIMODEL/STL/GLB、七视图和原生 OBJ 生成/菜单导出已有代码与定向验收；旧 Quick 模板语法、禁用特征兼容、原生 CATIA 导出和桌面/Agent端到端仍待完成 |
| F5.1 | Rust 七视图 PNG 与深度遮挡 | **已完成** | 五项定向测试、真实 OCCT 1000 三角形 CLI 七视图通过；非 Agent 图片验收 |
| F5.2 | Rust AIMODEL / binary STL 兼容读写 | **已完成** | 5 项单测、旧工程 roundtrip、Python Document 对拍及实际 STL 全顶点检查通过；Quick 和生产导出接线尚待完成 |
| F5.3 | Quick 明确尺寸原生创建闭环 | **已完成（限定范围）** | Rust Quick 3/3、解析 7/7、Vue build；CADPlan→OCCT→GLB/AIMODEL/STL，旧语法全量迁移和共享活动状态仍待 F7.G2；见 [记录](./27-f5-native-quick-build.md) |
| F6 | Python 缩减到 CATIA COM | **进行中** | DSH Agent 的 Python tool worker 已删除；桌面初始化、设置/provider、parts/samples、联网/MCP仍调用 Python bridge，安装仍带科学/CAD依赖；需逐项迁移服务并把 Python 收敛到 CATIA COM |
| F7 | 多模态盲测与科学验收基线 | **进行中** | 生产同源冷启动 runner 已执行一轮真实原图盲测，模型候选因孔位/视图复核未通过而被拒；仍需修复并完成图片、纯文字、多轮、返修、取消及 CATIA 分层验收 |

## 已确认的 Agent 链路治理

2026-10-03 用户确认：以下八项全部纳入 Todo，**P0 优先**。感知、建模、审查允许分别选择模型/provider，默认共用当前配置；建模优先短推理，复杂识图与审查按需增强。继续使用 DSH 原生 Cordis 插件。详细依据与验收见 [Agent 链路治理卡](./25-f7-agent-chain-governance.md)。

| ID | 优先级 | 任务 | 状态 | 完成条件 |
|---|---|---|---|---|
| F7.G1 | P0 | 主 Agent 直接查看原图和当前单视图 | 代码完成；原图经视觉工具真实传输，主对话模型直收证据仍缺 | 无条件剥离已移除，mock wire 7/7、schema契约1/1。实跑证明原图以精确 SHA 到达本地 source inventory 和图像审查请求；当前 runner 没有单独记录主 DSH provider 的图像 wire，故不能宣称主对话模型本身已直接收到原图；视觉审查还误报了左面孔位，详见 [实测](./35-f7-image-acceptance-run-01.md)。 |
| F7.G2 | P0 | CADPlan、Agent 与桌面模型统一同步 | **核心代码已提交；定向检查通过，真实桌面/Agent端到端待验收** | Agent/Quick/特征编辑、活动模型恢复、Save As、STL/OBJ/report/多视图快照和显式旧工程转换都路由到活动模型；Agent 几何工具通过 generation CAS 防止旧会话覆盖，CAS 失败时会话/证据/预览会回同步到活动记录。定向离线测试与 Cargo check 通过。CLI/无头预览不能代替真实 DSH→`model.changed`→桌面显示的端到端验收；启停与 CATIA 原生导出仍明确受限。见 [活动模型同步记录](./38-f7-active-model-sync.md)。 |
| F7.G3 | P0 | 请求结束与模型验收状态区分 | 代码完成，端到端待验收 | Host 9/9、Vue build通过；turn_status独立，旧finish不得accepted，旧preview显示stale，纯问答不称建模完成；真实四场景见 G8，记录见 [状态真值](./29-f7-turn-and-model-status.md) |
| F7.G4 | P0 | 阶段复核与施工范围一致 | 代码完成，真实视觉待验收 | 宿主revision/ID范围、out_of_scope、全当前几何覆盖门槛已接入；定向集成通过，不完整范围只能诊断不可贡献覆盖；真实返修待 G8；见 [记录](./32-f7-stage-review-scope.md) |
| F7.G5 | P1 | 观察因冲突可重新读取、版本可追踪 | **代码完成，真实视觉链路待验收** | 普通同 SHA/ROI 复用缓存；Agent 显式传 `conflict`/`reobserve` 才重新观察并递增版本；保留历史、SHA/ROI/reason/telemetry；新版 evidence ID 隔离旧 review/finding。定向测试 11+5+3 通过；见 [记录](./34-f7-observation-refresh.md)。 |
| F7.G6 | P1 | 统一实际工具与几何能力契约 | **代码完成，运行期遵循待 G8 验收** | 26 类注册特征统一公开本地 exact/approximate/unsupported、支持/拒绝变体和参数边界；preflight 共用同一 native kind registry；面工具 source_type 与 hole-array 上限对齐。3 项定向测试已由 subagent 跑通；`parameter_limits` 当前为说明型契约，后续需随 preflight 同步。见 [记录](./36-f7-native-capability-contract.md)。 |
| F7.G7 | P1 | 分阶段模型、推理预算及行为熔断 | **代码完成、定向检查通过；真实DSH多模态链路待验收** | 感知/建模/审查可独立选择供应商与推理强度，空或空白 provider ID 继承全局当前配置；ACP reasoning 仅在回执给出有效值时标记生效，未确认会告警。视觉请求遥测明确记录发送前预期值，不声称服务端已生效；未知 Chat Completions 路由不发送通用 reasoning 字段。Agent turn 使用 60 分钟总时限、10 分钟无有效进展时限、15 秒取消宽限；相同写工具/脱敏参数且 revision 未变化或错误重复 5 次才触发无进展熔断，不设总工具/返修次数上限。超时后回收挂起 ACP 请求；用户取消、超时、无进展与 Host 错误分别记录，终态单点发出，所有退出路径清理 active。定向 cargo check、策略单测、ACP pending 请求释放 fixture、AgentHost、阶段空值和视觉路由测试以及前端配置往返测试通过；fixture 未覆盖完整 Tauri watchdog 回路。未做真实 DSH/LLM 多模态验收。阶段配置存于全局桌面设置，不随 `.aimodel` 工程保存。见 [阶段模型与运行熔断记录](./39-f7-stage-routing-watchdog.md)。 |
| F7.G8 | P1 | 真实多模态与多轮链路可复跑验收 | **已完成 1 次真实图片负样本；调用审计已实现，整体未通过** | 原图真实传输、DSH工具执行和多模态review均发生；review错误地声称左侧有孔，且所有返回 `evidence_id` 为空/未映射，因此宿主 coverage 为 partial、`accepted=false`。hole-array 调用后 revision 未变，历史轨迹缺少原始参数/结果，无法追溯该次调用原因。后续 native 工具调用会写入 `native-tool-audit.jsonl`，记录脱敏参数、执行结果/错误与 revision 前后值；这只能改善未来归因，不能补回历史证据，也不等于模型意图或几何已验收。见 [冷启动实测 01](./35-f7-image-acceptance-run-01.md) 和 [工具调用审计](./37-f7-native-tool-audit.md)。修复后仍需重跑单图、多图、文字、多轮返修、取消与 CATIA 独立验收。 |

执行顺序：先完成 P0 G1–G4 的缺口，再推进 G5–G8；F4 完整几何、F5/F6 原生状态迁移同时继续。生产同源冷启动 runner 已执行并记录一轮失败样本，不代表整个 G8 或 F7 通过。F7 总体只有所有链路整改和真实验收完成后才可标记完成。

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

F4.1 的真实 SDK、DLL 生命周期缺陷和几何检查记录见[固定 OCCT SDK 与 Rust 几何基础](./10-f4-native-occt-foundation.md)。

F4.2.1 的坐标契约修复、实际参数对拍和测量边界见[CADPlan 基础特征原生执行](./11-f4-basic-plan-execution.md)。
