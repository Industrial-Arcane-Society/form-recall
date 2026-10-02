# Rust + DSH 重构总 Todo

更新时间：2026-10-02。每一项 feature 按边界实现、验收并单独提交。首个“目录重构”feature 完成后直接提交到 `main`；后续 feature 使用独立提交，不把未验收工作合并。

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
| F2 | DSH 原生 Cordis CAD 插件去 Python worker | **进行中** | Cordis schema 已由 Rust 持有，工具请求经过 Rust 本机桥；仍需迁出 Python CAD 状态/执行与图片观察，并完成真实 DSH 工具调用验收 |
| F2.1 | Cordis → Rust 本机工具桥 | **已完成** | Cordis 原生注册与 Rust Host 的认证 JSON-RPC、取消、超时、去重和图片返回链路测试通过；Rust 下游仍调用旧 Python worker，故不代表 F2 完成；提交 `501d47b` |
| F2.2 | Rust 持有 CAD 工具 schema 与参数契约 | **已完成** | Rust 内嵌 25 项 schema，与迁移时 Python 函数输出逐字段相同；不再启动 Python 取 schema；固定 DSH `defineTool()` 成功注册 25 项 |
| F2.3 | 只读状态、知识及联网工具迁入 Rust Host | **已完成** | 六项原生读取工具已接入 Rust；生产测试 55/55，实际 DSH CLI 完成五项调用且禁止 Python 回退；外网搜索因本机 DNS 非公网解析失败，如实返回，正文成功路径以 HTTP fixture 验证 |
| F2.4 | 图片观察与视觉复核迁出 Python | **进行中（原生接线已提交）** | domain/executor 原生接线完成；真实 DSH 观察工具及原图传输通过，完整复核质量仍待 F7 |
| F2.4.1 | Rust 多模态 provider 与图片资产 | **已完成（传输通过，观察质量未通过）** | 双协议、取消、SHA 遥测、EXIF/ROI 输入已实现；真实原图请求成功，观察错误作为后续负样本保留 |
| F2.4.2 | 原生视觉 domain 与 Cordis 接线 | **已完成（接线/传输）** | 真实 DSH cad_inspect_references、SHA、Responses、禁止 Python fallback 通过；单视图复核为 fixture 验收，不冒充质量通过 |
| F2.5 | CAD 写工具迁移到 Rust CAD domain | 未开始 | 建立/修改/删除/预览/构建工具直接调用 F3 Rust CADPlan，移除 Python tool worker 兼容执行器 |
| F3 | Rust CADPlan 与增量模型事务 | **进行中** | 纯增量事务已移植；完整参数校验、会话版本/回滚及 preview/build dispatch 尚待接线验收 |
| F3.1 | Rust CADPlan 纯增量事务与几何指纹 | **已完成** | create/add/update/remove/snapshot 保留来源与扩展字段；数值更新来源 guard、失败原计划不变；140 组 Python 几何指纹对拍一致，精确浮点解析已启用 |
| F3.2 | Rust CADPlan schema 与静态校验 | **已完成** | 覆盖 27 类特征、条件参数、来源、关系、单位/坐标和静态支持状态；27 个 Python golden 双接口对拍通过，strict 执行前阻断已验证，未执行几何或 CATIA |
| F3.2.1 | 布尔 base 校验迁移缺陷修复 | **已完成** | 实际几何接线暴露合法 base 被误当长度拒绝；补 8 个真实 Python 校验样例，Rust 对拍通过 |
| F3.3 | Rust CAD 会话事务与 preview/build dispatch | **进行中** | 会话版本、候选/回滚、来源及差异生命周期正在移植；真实几何回传和端到端验收尚待 F4 接入 |
| F3.3.1 | 会话事务及版本/来源/验收门禁 | **已完成（domain）** | 16 项定向正负测试通过；真实后端及 Cordis 写工具接线尚待完成，复杂来源语义仍有限制 |
| F3.3.2 | 宿主整体复核与完成门禁 | **已完成（domain）** | session 18/18、gate 7/7；旧版本/篡改证据阻断，当前未验收候选可保存；真实视觉质量待 F7 |
| F4 | Rust/C++ thin OCCT FFI | **进行中** | 固定官方 OCCT 7.9.3 SDK 已构建，C ABI/Rust 包装已通过真实几何 fixture；CADPlan 全特征执行和安装交付仍未完成 |
| F4.1 | 固定 OCCT SDK、C ABI 与 Rust 几何基础 | **已完成** | 实际 Rust → C ABI → OCCT fixture 通过，正常退出；原生 27 类 CADPlan 执行及安装交付仍待完成 |
| F4.2 | CADPlan 基础特征到 OCCT 的原生执行 | **进行中** | 五类特征执行与旧内核参数对拍正在验收；其余类型、孔位独立测量和生产 dispatch 尚未完成 |
| F4.2.1 | 五类基础特征与参数坐标对拍 | **已完成** | box/cylinder/sphere/hole/pocket 的基础变体已执行；24 组真实几何对拍、4 个负样本通过，孔记录未冒充独立测量 |
| F4.2.2 | 圆锥、棱柱和回转能力 | **已完成** | 三类原生构造器及单位/坐标接线完成；含负角回转的 44 组旧内核几何对拍通过 |
| F4.3.1 | B-Rep 材料区间与离散孔证据基础 | **已完成** | 精确面交事件、Rust RAII、41 射线 pre/post 对比；真实九孔/盲孔/错位/错径正负样例通过，非孔径计量，生产接线待完成 |
| F4.3.2 | 孔材料采样接入 CADPlan | **已完成** | 44 对拍、6 负样例、局部材料盲孔与近边孔部分证据通过；采样不冒充孔径计量 |
| F5 | Quick 模式与本地导出迁移 | **进行中** | 原生单视图渲染基础完成；Quick、生产预览接线及 AIMODEL/STL 兼容性尚待完成 |
| F5.1 | Rust 七视图 PNG 与深度遮挡 | **已完成** | 五项定向测试、真实 OCCT 1000 三角形 CLI 七视图通过；非 Agent 图片验收 |
| F5.2 | Rust AIMODEL / binary STL 兼容读写 | **已完成** | 5 项单测、旧工程 roundtrip、Python Document 对拍及实际 STL 全顶点检查通过；Quick 和生产导出接线尚待完成 |
| F6 | Python 缩减到 CATIA COM | 未开始 | Python 包只剩 CATIA Windows/COM adapter 和必要协议代码；其余业务逻辑和 Agent 代码不再由 Python 执行 |
| F7 | 多模态盲测与科学验收基线 | 未开始 | 图像/文字盲测、工具轨迹、几何量测、视觉差异关闭、真实 CATIA 证据分别归档并可复跑 |

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

F4.1 的真实 SDK、DLL 生命周期缺陷和几何检查记录见[固定 OCCT SDK 与 Rust 几何基础](./10-f4-native-occt-foundation.md)。

F4.2.1 的坐标契约修复、实际参数对拍和测量边界见[CADPlan 基础特征原生执行](./11-f4-basic-plan-execution.md)。
