# F7.G1 Run36：配对预览冷启动资源核验（未启动）

日期：2026-10-05

## 运行条件与边界

- 初建隔离 worktree `C:\Users\lycohana\worktrees\f7-g1-run36` 时，`main` / `origin/main` HEAD 为 `45abc8fda915d2f30588e3e404232c00e9bb9e84`，分支 `feat/f7-g1-run36-paired-view` 且创建后 clean。任务进行期间 `origin/main` 更新至 `b0c535c`；本报告提交已 rebase 到最新 main。此为资源核验记录，没有针对任何 commit 发起 CLI 冷启动。
- 计划验收对象是最新 `cad_preview_model(reference_id)` 路径：同一个 Cordis tool result 先附原图、再附指定当前单视图，并分别绑定 reference SHA 与 preview revision/generation/SHA。预期只传 runner 四个必需参数，省略可选图片与 user text，使用默认原图和默认 prompt；不注入 CADPlan、尺寸、孔数或其他 seed。该运行没有启动。
- Run35 所用 `f1-runtime-probe` 目录已不存在。root main staging 当前 `dsh-runtime-win-x64.zip` 为 198,590,382 bytes、SHA-256 `6A388F3757A4F75D8A92D901598B2FF9DF03ABD8EE76A30731FEA99BE224F57D`；伴随的 `runtime-bundle-manifest.json` 将 bundle SHA/size 及 Node `v22.23.3`、npm `10.9.9`、DSH/ACP `0.2.0-rc.2`、ACP SDK `1.4.0` 固定在同一清单中，bundle 值与实测一致。仓库的 [`dsh_runtime.rs`](../../src/apps/desktop/src-tauri/src/dsh_runtime.rs#L320) 校验随包 bundle manifest 的版本和 SHA；tracked [`runtime-manifest.json`](../../src/legacy/packaging/dsh-runtime/runtime-manifest.json#L5) 固定 Node/DSH/ACP/npm 输入版本及 package/lock hashes。没有发现仓库中另一个要求旧 zip SHA `6A388F...DA24A` 的权威 pin；它来自先前状态，已由 root 确认过期。
- zip 条目含 `node/node.exe` 与 `dsh/node_modules`，目录布局与 runner 入口一致（runner 检查 Node `v22.23.3`、DSH `0.2.0-rc.2`；[`native_agent_coldstart_acceptance.mjs`](../../src/apps/desktop/src-tauri/examples/native_agent_coldstart_acceptance.mjs#L115)）。本次只读核查了归档路径和随包元数据，没有解包或单独启动该 Node 二进制。
- root main staging 的 OCCT manifest 为 3,707 bytes、SHA-256 `F4AD33AC7AF579440045C8465B9B6E7E4CA0193EEAE4A3D4F33782A9E2C4E617`。仓库中未发现要求旧 SHA `e8c654...` 的权威 pin；[Run33 记录](./100-f7-g8-run-33-diagnosis.md#L10) 曾以完全相同的实际 SHA 使用此 manifest。manifest 本身带 OCCT source/archive 与各 runtime artifact 的指纹；此处只读取其结构，未启动 native probe 核验运行时加载行为。
- 未创建运行输出目录，未触发 native probe、offline build、DSH/provider request；无运行产物可审阅。没有读取 raw provider logs、thought、scratch、工具参数或几何载荷。

## 结果与限制

本记录是资源/源码前置条件核验，**不是 Run36 冷启动结果**。没有发生哈希不匹配；Run35 临时 runtime 虽已清理，但当前 main staging 中存在随 bundle manifest 锁定、与 runner 入口兼容的 pinned Node/DSH 资源。当前 staging OCCT manifest 有内容级指纹，且同一文件 hash 出现在 Run33 记录中。仍未执行 CLI、offline build、native probe、DSH/provider request；因此 `cad_preview_model(reference_id)` 的实际调用、原图/单视图是否被消费、host gates、transport 以及独立图像视觉判断均未验证，不能据此判定 F7.G1 通过或失败。

Todo 状态保持“本地契约通过；真实多模态视觉验收未通过/待完成”。后续真实冷启动仍需按 Run35 流程在本分支的隔离目录解包 pinned runtime、使用新输出目录，并只运行一次；需另外保留安全 allowlist artifacts，且将 host/transport gates 与独立图像判断分开汇报。
