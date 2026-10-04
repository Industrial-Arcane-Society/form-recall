# F7.G5 跨 observation 重绑人工确认

日期：2026-10-05

## 修复范围

`native_review_findings::reconcile` 不再把 provider 声明的唯一 `source_evidence_id` 当作跨 observation 的宿主身份锚点。该 ID 仅用于生成明确的人工 adjudication 候选；finding 保留原 evidence 与 mismatch binding，并进入 `confirmation_required`。mismatch review 的跨 observation 续接也遵循同一规则。

人工确认仍通过既有 generation/revision CAS 路径，确认只记录来源关系并保持 `awaiting_verification` / `resolved=false`。finding 只有在更晚 CAD revision 上收到新的、完整且独立的 review，满足原有 preview/pair freshness 与 stage coverage 条件时才可关闭。同 observation 的复核和关闭逻辑保持不变。

## 定向回归

- 唯一、空及 fallback provider identity 均不能自动跨 observation 关闭 finding；候选只能进入人工确认队列。
- 跨 observation 的 mismatch 不迁移原 finding 的 evidence/binding，且保持待处理。
- CAS 人工确认后仍未解决；在 adjudicated revision 重放旧 review 不关闭，只有更新 revision 上的新完整 review 才关闭。
- 现有同 observation 的关闭路径由原有 finding tests 覆盖。

## 验收边界

`cargo test --offline --lib native_review_findings -- --test-threads=1` 编译了库和测试 EXE。需用 `TAURI_CONFIG={"bundle":{"resources":[]}}` 绕过本 worktree 没有打包资源目录的 build-script 检查；之后测试 EXE 未启动，Windows 返回 `0xc0000139 STATUS_ENTRYPOINT_NOT_FOUND`，因此本轮没有 Rust 断言结果。`git diff --check` 通过。针对该文件的 `rustfmt --check` 仍报告基线已有 import 排序及两处未修改测试代码格式差异；没有执行全文件或全仓格式化以免引入无关改动。未运行真实 DSH/provider，也未据此宣称 G5 的端到端运行验收完成。
