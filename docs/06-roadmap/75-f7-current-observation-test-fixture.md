# F7 当前观察绑定测试夹具复核

## 问题

2026-10-04 集成分支 Rust 全量测试首次为 322/326；4 个失败集中在 `native_cad_session` 的图像复核与 finish 用例。复核失败样本后发现，公用的 `image_state_with_coverage` 测试夹具仍使用旧版 review 记录：它没有同时绑定原图 `source_sha256` 和当前 `observation_id`。生产代码要求复核 source SHA 与当前观察版本一致，并且只将绑定当前观察的 review 视为可比较证据。因此这个过期夹具会被正确判为不可比较，导致 coverage/整体验收为 partial。

## 修复与验证

- 更新测试夹具，使 review history 与 `reference_inventory` 共享同一 `source_sha256`、`observation_id` 和 ROI 绑定。
- 不放宽生产 acceptance gate，也不改图像覆盖规则。
- `native_cad_session::tests::` 定向运行通过 **23/23**，包括原来失败的四个用例。
- 全量 Rust 回归尚待再次运行；该定向结果不替代完整套件结果。
