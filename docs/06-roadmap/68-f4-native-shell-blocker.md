# F4.2.20：CADPlan shell 原生执行阻塞记录

## 结论

本项没有实现 Rust/OCCT shell builder。当前 CADPlan 与 legacy `Document` 没有足够语义决定抽壳后哪些面应打开以及壳体向哪侧偏置。任意选择一个或全部面、默认向内，或把结果做成封闭空心体，都会替计划补写设计意图并可能改变拓扑。因此 `shell` 继续保持 native unsupported；不得用一个 generic hollow-shell 构造器宣称完成 F4。

## 代码核查事实

- `src/legacy/ai_modeler/sdk/cadplan.py` 的 `FEATURE_SPECS["shell"]` 只声明参数 `thickness`，状态为 `unsupported`；通用 CADPlan feature 对象的字段是 `id/kind/operation/parameters/sources/relations`。`relations` 仅支持 `depends_on`、`pattern_source`，都不是面引用。
- `cadplan.py` 将 shell 厚度按长度单位归一化，但单位换算不会提供被移除面的身份或偏置侧。
- `src/legacy/ai_modeler/core/model/docmodel.py` 的 `Feature.build` 没有 `shell` 分支，故 legacy polygon `Document.build()` 路径不能作为 shell 几何 oracle。
- `src/legacy/ai_modeler/core/geometry/occ_kernel.py` 将 shell 列为 `unavailable`，执行分派直接抛出 `NotImplementedError`，理由是 CADPlan 未指定要移除的面、缺少可执行的抽壳边界。
- 当前 Rust 原生能力契约只应报告 `shell` 为 unsupported；`impeller` 是另一个未实现 kind。该能力状态不是构造器实现或正例验收。

## 最小 schema 缺口

恢复实现讨论前，至少需要明确并验证以下输入：

1. shell 操作目标对应哪个既有实体/特征，避免隐式对不确定的当前形体操作。
2. 一个或多个要移除面的可复现引用。单独使用 OCCT 面序号不稳定；schema 需要稳定拓扑命名，或定义可验证且唯一的语义几何选择器，并规定零匹配/多匹配时必须失败。
3. 壳厚及偏置方向（向内、向外或双向）的明确语义；单位换算和符号约束需进入校验。
4. 操作结果允许的开口边界与连通/实体约束，明确多区域、偏置自交、零厚度/局部失效时的拒绝行为。

补齐 schema 后还需确认 legacy/CADPlan 执行语义，或以显式迁移说明废弃旧语义。不能仅因 OCCT 有 `MakeThickSolid` 一类 API 就推断 CADPlan 意图。

## 本轮验收边界

- 仅完成 schema、legacy 与 Rust 能力契约的只读核对，以及本阻塞记录、Todo 和能力矩阵说明。
- 不新增 native builder，不改运行时状态，不把 unsupported 变体送入几何执行。
- 此记录没有几何正例、Python/OCP parity、实体有效性或拓扑结果；相关验收只有在 schema 缺口解决后开展。
