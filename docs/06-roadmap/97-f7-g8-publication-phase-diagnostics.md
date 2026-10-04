# F7/G8 safe publication phase diagnostics

日期：2026-10-05

## 目的

Run32 的 safe failure diagnostic 仅表明 `stage=publication`，不能区分投影计算、某一项安全文件写入或文件全部写完后的 runner completion。此记录只描述新增诊断设计，不推断 Run32 实际异常发生位置；该次 publication exception 的根因仍未知。

## 固定诊断边界

`failure_diagnostic.phase` 只允许以下固定值：`not_started`、`projection`、`writing_diagnostics`、`writing_build_status`、`writing_summary`、`publication_complete`、`runner_completion`、`unknown`。runner 在严格门禁计算完成后进入 `projection`，publisher 在每次安全文件写入前更新相应 phase，全部文件写完后设置 `publication_complete`；runner 随后切换到 `runner_completion`。

输出只包含固定 `stage`、`phase`、`code` enum。无效 phase 会变成 `unknown`；异常正文、stack、文件路径、provider 内容、工具参数/ID、scratch 信息以及图像/几何载荷均不进入此诊断。

## 兼容修正与回归

静态检查发现 `safeSummary` 原先声明在局部块中，却在块外用于读取最终状态；现改为 runner 作用域变量并在发布前赋值。这是代码路径修正，未将其归因为 Run32 的 publication exception。

合成 Node 回归检查固定 phase 集合、各写入边界顺序、发布完成与 runner completion 的区别，以及不受信任 phase/code 字符串被降为安全 enum。publication 中途失败回归确认摘要保留 `writing_build_status`；测试输入中的 sentinel 不会出现在输出中。此测试不触发 DSH/provider，也不读取运行记录。

验证：coldstart Node gate suite 131/131；`node --check`、脚本检查与 `git diff --check` 结果记录在对应 feature 提交说明中。phase 诊断仅帮助定位代码阶段，不揭示具体异常原因，也不构成视觉、几何或 CATIA 验收。
