# F7/G8 Run36：一次冷启动的安全诊断

## 运行范围

- Run36 于 2026-10-05 11:42:13 UTC 在固定代码 HEAD b0c535c45bcb018a5f13725745ceee5809ac8fa2 启动，12:04:58 UTC 结束。只运行一次，采用默认原图和提示词，不传可选图片或用户文本，不注入 CADPlan 或尺寸 seed，没有重试 provider 请求。
- 只读取 safe-output 中的 summary.json、build-status.json 和 tool-result-diagnostics.jsonl；没有读取 raw provider logs、scratch、模型/几何载荷，也没有进行或宣称独立图像对比。
- 原图 SHA-256 为 53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553，运行前后未变。scratch_cleanup_status 为 removed；safe-output 保留于 %TEMP%\f7-g1-run36-20261005-194213-805a5d7493354f9593fe23434717ac6b\safe-output。

## 已观测结果

- runner exit code 为 1；summary.status 为 failed，scope 是 single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance。
- Rust/Cargo build-status 为 passed、exit code 0、elapsed 3,817 ms。DSH native probe 为 ready_success，transport 为 completed，model 为 candidate；prompt elapsed 为 1,337,424 ms（约 22.3 分钟）。这是本地程序构建通过、Agent 回合产出 candidate 状态；current_geometry 与 artifacts gates 失败，不能据此说几何有效或最终模型成功。
- 13 个 strict gates 中 6 passed、7 failed。通过项：run_scope、dsh_session、dsh_process、trace_integrity、original_image、main_image_wire。失败项：cordis_cad_tools、acp_cad_tool_results、current_geometry、artifacts、holes、visual、finish。总 failure reason count 为 40，evidence error count 为 0。
- 共有 106 个有效 session updates、36 个工具调用/更新，其中 14 completed、22 failed；几何 revision 前进 3 次。visual_review_projection 的 review_count 为 0。因此本次没有可引用的独立多模态复核，也不能判断候选模型是否与原图相似。
- current_geometry 的失败投影包括最新 finish/review fingerprint 过期、attestation/binding 不匹配、native geometry check 未通过和 partial preview 不可作为完整候选；artifacts 报告当前产物缺失/无效且发布 CADPlan 不一致；holes 有未完成或未验证测量；visual 报告原图 inventory、coverage、独立 review、manifest 或 mismatch 条件未满足；finish 显示 host 未接受且仍有未解决项。各项只按固定安全 reason projection 摘要，不读取底层自由文本或几何载荷。
- Cordis/Rust bridge diagnostics 出现 bridge_execution_rejected，也出现 native_audit_rejected（cad_create_model 为 state_conflict；inspect、preview、remove、review similarity 等有 execution 类记录）。ACP→bridge 匹配只证明调用身份映射，不证明 Rust handler 已成功完成。只读源码审查未找到能据此确认的单一确定实现缺陷；现有摘要无法区分 executor 前校验/期限拒绝、桥接响应/协议问题，或 native audit 关联/写入问题。

## 结论与后续

Run36 是启动、传输和构建都已发生但 host gates 整体失败的候选运行；它不是模型通过，也不是独立视觉或 CATIA 验收。provider request 没有重试。下一步优先增加不含调用 ID、参数、指纹和错误正文的桥接阶段安全投影：固定枚举记录 dispatch stage、failure class、耗时/字节数分桶、native audit 是否写入及 audit 关联失败类别；再以同一调用对应的 Rust 摘要和现有 ACP/Cordis/audit projection 定位拒绝层。Run36 不重跑；F7/G1/G8 与独立视觉验收仍未通过。

配套的资源前置核验见 103-f7-g1-run-36-preflight.md。Run36 执行与本报告对应 Todo 见 01-migration-todo.md。