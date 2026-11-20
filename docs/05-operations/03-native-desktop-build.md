# 原生桌面构建与目录约定

新桌面入口位于 `src/apps/native-desktop`，Go 模块锁定 MyGo v0.9.1，使用项目内 Go 1.27.4 工具链。界面与系统窗口由 MyGo 管理。`src/apps/engine-host` 提供常驻 Rust JSON Lines 进程，复用 `src/core/cad-engine` 的建模、工程状态和 Agent 功能。

## 本地构建

Windows x64 需要 Rust MSVC 工具链和 Visual Studio C++ Build Tools。Go 可以使用固定 SDK，下载、SHA-256 校验及解压都在仓库 `.tools` 中完成。脚本不修改全局 PATH 或 Go 配置。

```powershell
./scripts/bootstrap-go.ps1
./scripts/build-native.ps1 -Offline
./scripts/test-native.ps1 -Offline
./scripts/run-native.ps1 -SkipBuild -OcctManifest '<OCCT目录>/runtime-manifest.json'
```

默认构建 GUI release 版，输出到 `temp/outputs/native-desktop/release`。`-DebugBuild` 输出带控制台的 debug 版。Cargo 的默认缓存为 `temp/build-cache/rust-native`，可用 `CARGO_TARGET_DIR` 覆盖。

`-Offline` 要求依赖已在本机缓存，Cargo 使用离线模式，Go 禁用模块代理下载和自动工具链下载。首次准备依赖时运行不带 `-Offline` 的构建命令。

测试脚本执行 Go 依赖核验、`go vet`、Go 测试、Rust 核心库测试和引擎进程测试。定位历史失败可使用 `-RustFilter`，单独运行某一语言可使用 `-SkipRust` 或 `-SkipGo`，交付记录应注明筛选范围。

根目录 `npm run dev` 进入新原生桌面。`npm run dev:tauri` 启动兼容的 Vue/Tauri 桌面，`npm run dev:web` 保留浏览器开发预览。

原生资源准备使用 `build_desktop_resources.mjs --native-resources`，与 `--dsh-only` 共用 `temp/build-cache/dsh-runtime-package` 缓存。下载归档位于 `package-downloads`，准备好的资源位于 `package-resources`，其中 `app` 保存提示、知识和 DSH 离线包，`python` 保存嵌入式 Python，`catia-bootstrap` 保存 pywin32 wheel 与 CATIA 运行时清单。首次准备可单独执行以下命令，缓存齐全后可追加 `--offline`。

```powershell
node src/legacy/scripts/build_desktop_resources.mjs --native-resources
```

`run-native.ps1` 将 `app`、`python` 和 `catia-bootstrap` 完整部署到当前构建输出目录，与 `formrecall-engine-host.exe` 并列。Release 的实际资源入口为 `temp/outputs/native-desktop/release/app`，debug 使用对应的 debug 目录。宿主据此识别 packaged 资源，并从自身目录找到 CATIA 依赖。已匹配版本、锁文件和 SHA-256 的 DSH 包及 Python 文件直接复用，小资源文件按源码同步。缓存缺失或失效时执行完整原生资源准备。传入 `-Offline` 禁止下载，缺少所需缓存时明确报错。OCCT 通过已校验运行时的 manifest 路径加载。

## 运行时和打包

OCCT 必须通过 runtime manifest 校验。可以传 `-OcctManifest`，设置 `AIMODELER_OCCT_MANIFEST`，或把已构建运行时放到 `temp/build-cache/occt-runtime`。脚本也会查找已有 Tauri 本地构建与资源暂存目录。

```powershell
./scripts/package-native.ps1 -SkipBuild -OcctManifest '<OCCT目录>/runtime-manifest.json'
```

每次打包生成新的目录及 ZIP，包含 Go 桌面、Rust 核心进程、必要提示与知识资源、DSH 离线包、CATIA 离线依赖、OCCT DLL 和许可证。Go 依赖许可位于 `licenses/go`，SVG 图标完整许可位于 `licenses/icons/Lucide-LICENSE`。

### 程序目录布局

打包产物、便携包解压目录和安装目录使用同一套布局：可见根目录只有启动入口和 `runtime` 目录。

```text
FormRecall/
├─ FormRecall.exe        # 与 runtime/base/FormRecall.exe 逐字节相同
└─ runtime/
   ├─ base/              # 随包提供的版本：桌面、引擎、OCCT、资源、许可证
   └─ versions/          # 首次应用更新后出现：当前版本、一个回滚版本和状态文件
```

`runtime/base` 内部仍是桌面、Rust 引擎和 OCCT 原有的相对路径，所以更新只需把整份版本目录放到 `runtime/versions`，不必改写程序内部路径。根目录启动入口只选择版本：优先启动已激活的更新版本，没有更新时启动 `runtime/base`，两者都不可用时直接报错提示重新安装。

应用更新在 `runtime/versions` 下组装并验签。第一次更新成功后 `runtime/base` 作为回滚版本保留，第二次激活时连同更早的目录一起删除，因此安装目录最多保留两份完整程序，不会随更新次数持续增长。基础版本的完整性参照 `runtime/base/package-manifest.json`，更新版本继续使用签名清单。

`package-manifest.json` 位于 `runtime/base`，记录源码提交、工作区是否有未提交变更及该版本全部文件 SHA-256。

CATIA 离线依赖固定为 CPython 3.13.16 x64 嵌入包和 pywin32 312 wheel，下载时分别核对固定 SHA-256。`catia-bootstrap/catia-runtime-manifest.json` 记录 Python 全部文件的大小与哈希、wheel 哈希及依赖清单哈希，打包校验同时检查必需文件、路径安全和目录内额外文件。Python 许可证位于 `python/LICENSE.txt`。首次 CATIA 操作将依赖安装到当前用户数据目录的 `runtime/catia-python`，有效安装会在后续操作中复用。实际 CATIA 导出和推送需要本机已注册的 CATIA COM。

清单的 `source_dirty` 包含 Git 检测到的已跟踪变更和未忽略的未跟踪文件。`source_tracked_dirty` 单独记录已跟踪变更，`source_untracked_count` 记录未跟踪文件数。因此已有的 `temp/experiments` 文件也会使 `source_dirty` 为 true，这些状态字段描述打包时的实际工作区。

打包脚本最后自动核验目录及 ZIP 的文件清单、文件大小和 SHA-256，并检查两个程序为 Windows x64 PE32+。Release 桌面使用 GUI 子系统，debug 桌面和引擎使用控制台子系统。随后自动执行 DSH 与 CATIA 的隔离安装验证。复核已有产物时，可通过两个安装开关执行相同检查。

```powershell
./scripts/verify-native-package.ps1 -PackageDirectory '<包目录>' -ArchivePath '<ZIP路径>' -InstallDshRuntime -InstallCatiaRuntime
```

`verify-native-catia.ps1` 也可单独验证已部署完整资源的运行目录。它在临时数据目录内启动真实宿主，完成 Quick 建模后，通过 CATIA 导出的输出扩展名检查触发私有运行时准备，再检查安装标记、重复准备和 Python/pywin32 模块加载。注册状态通过只读注册表查询记录，证据中的 `catia_export_verified` 保持 false。验证结束后清理临时数据，结果写入 `temp/outputs/native-catia-verification.json`。

```powershell
./scripts/verify-native-catia.ps1 -BundleDirectory 'temp/outputs/native-desktop/release' -OcctManifest '<OCCT目录>/runtime-manifest.json'
```

运行环境可设置以下路径。

| 环境变量 | 用途 |
| --- | --- |
| `FORMRECALL_ENGINE` | Rust 核心进程绝对路径 |
| `AIMODELER_RESOURCE_ROOT` | 提示、知识和运行时清单资源目录 |
| `AIMODELER_OCCT_MANIFEST` | OCCT manifest 绝对路径 |
| `AIMODELER_DESKTOP_DATA_DIR` | 独立的用户数据目录 |

`run-native.ps1 -DataDirectory` 可指定验收数据目录。脚本只在启动子进程时覆盖环境，随后恢复调用终端原有值。日常数据与编译输出分别保存，构建及打包不会清理用户文档。

## Actions 版本发布

在 GitHub Actions 中选择 **Prepare Release**，点击 **Run workflow**，选择 `main`。`bump` 默认为 `patch`，也可填 `minor`、`major` 或具体版本，例如 `0.3.0-rc.1`。流程按根目录 `VERSION` 与已公开版本计算下一版本，创建 `Release v{version}` PR，并启动 Windows 安装包和便携包构建，供合并前下载审核。重复准备同一版本会复用已有 PR。

审核发布 PR 后合并到 `main`，**Publish Release PR** 自动将准确的合并提交交给 **Windows Release**。构建、安装、建模启动和卸载检查通过后才公开 Release。候选构建产物位于 Windows Release 的 Actions artifact，合并后的正式产物位于 GitHub Releases。带预发布后缀的版本自动标记为 prerelease。

发布 PR 只修改 `VERSION`，可追加该版本的 `.release-notes/v{version}.md`。桌面发布版本在构建时注入 EXE，`VERSION` 用于发布准备。日常开发构建继续接受 `-Version` 参数。Actions 使用 `GITHUB_TOKEN`，仓库需要开启允许 GitHub Actions 创建 PR 的设置，默认工作流权限保持只读，各流程按需申请权限。机器人创建的 PR 由 Prepare Release 显式启动候选构建。

需要直接构建时，可手动运行 **Windows Release**，填写 `version` 并选择 `main`。取消 `publish` 生成候选 artifact，保持勾选会在验证通过后发布。`source_commit` 可固定完整源码 SHA，留空使用所选分支或已有标签。合并后的自动派发失败时，可手动运行 **Publish Release PR**，填入已合并的 PR 编号重试。

工作流复用本地原生构建和打包脚本。首次运行从固定 SHA-256 的官方源码构建 OCCT，并准备 DSH 与 CATIA 的固定运行资源。后续使用 Go、Rust、OCCT 和资源下载缓存。Go 依赖检查、vet、普通测试、包目录与 ZIP 校验、DSH/CATIA 隔离安装、便携程序启动，以及安装包的实际安装、文件校验、四孔底板建模和卸载检查成功后才上传 Release。启动截图检验应用初始化，实际桌面显示的回归范围见验证记录。

GitHub 托管 Windows 运行器的启动检查使用 Mesa 26.0.3 的 llvmpipe 软件 OpenGL。工作流复制完整交付目录到运行器临时目录，为副本配置经过 SHA-256 校验的应用级 Mesa DLL，再检查视口和四孔底板。PR 检查使用相同驱动执行原生视口的帧缓冲、旋转、平移、缩放和动画测试。Release 附件由原始打包目录生成。

Release 提供以下文件。

| 文件 | 用途 |
| --- | --- |
| `FormRecall-{version}-windows-x64-setup.exe` | 完整安装包，向导安装全部运行组件并提供快捷方式和卸载入口 |
| `FormRecall-{version}-windows-x64-portable.zip` | 完整免安装包，解压后运行目录内的 `FormRecall.exe` |

两个下载文件的 SHA-256 写在 Release 说明中。构建 artifact 仍保留独立校验文件供自动验收使用。

安装包与便携包均包含桌面程序、Rust 建模引擎、OCCT 和运行资源。安装向导支持简体中文和英文，默认安装到当前用户目录，无需管理员权限，支持选择目录、开始菜单与可选桌面快捷方式，并提供系统卸载入口。安装包使用 Inno Setup 编译，Windows Actions 镜像预装编译器。本地版本打包需要可用的 Inno Setup 6，可通过 `-InstallerCompiler` 指定 `ISCC.exe`。指定 `source_commit` 时，源码需要包含完整安装包构建支持。历史已公开版本的重跑会直接跳过。

### 应用内更新

主仓库提供完整下载。[form-recall-release](https://github.com/Industrial-Arcane-Society/form-recall-release) 提供频道清单和 OTA 附件。Alpha、Beta 和 RC 按发布时间共享最近最多八个可升级的测试版来源。正式版从同版本线最近的 RC 和旧正式版计算增量。分发工作流复用验签通过的历史文件清单，合并内容相同的补丁。发布并验证附件后，更新签名频道清单。

主仓库发布完附件并完成校验后，会立即请求分发仓库的 `publish-ota.yml` 同步对应渠道，渠道由版本号推导（`-alpha`、`-beta`、`-rc`，其余为正式渠道），不再等待定时轮询。该请求只使用仓库 Secret `RELEASE_DISPATCH_TOKEN`，权限限于分发仓库的 Actions 写；签名私钥仍然只存在于分发仓库。同步失败会让发布作业标红，但不会回滚已发布的附件；此时在分发仓库手动运行 `publish-ota`（填入渠道和版本）即可补同步，因为发布作业在版本已发布后会被跳过，重跑它不会重新派发。本地直接运行发布脚本时若没有该 Secret，则跳过同步。

桌面客户端内置验签公钥，优先通过 GitHub 内容 API 检查签名清单。打开「设置 → 更新」，可选择 Alpha、Beta、RC 或 Release，并设置是否自动检测。偏好保存在用户数据目录。手动检测始终可用。选择同一版本线的较低渠道版本时，需主动选择「重启并降级」。若增量基线校验失败或没有匹配补丁，客户端改用完整便携 ZIP。两种路径都在独立版本目录组装并校验，启动器在建模引擎就绪后才提交新版本指针。启动失败时继续使用之前的版本。工程和用户设置沿用原有数据目录。

分发 Release 提供一份签名的 `update.json`，每个渠道使用一条固定的清单地址。按来源和目标版本命名 OTA 附件。多个来源共享同一附件时，发布时间线中最接近的来源用于文件名，全部适用来源列在发布说明和签名清单中。

Beta.3 将更新格式切换为单清单。旧版用户需要用完整包升级一次。Beta.3 可读取历史签名清单进行同版本线跨渠道降级，从使用旧格式的历史版本返回时，可将 Beta.3 便携包解压到新目录后启动。实际运行版本显示在更新页，Windows 卸载列表显示最近一次通过安装向导安装的版本。

新的目录布局（`FormRecall.exe` 加 `runtime` 目录）换掉了旧版把整份程序摊在根目录的做法。0.3.0 及更早的客户端在验签清单时要求根级引擎与 OCCT 路径，无法通过应用内更新进入新布局，因此本版本不写入 OTA 渠道清单。这类用户用完整安装包原地升级，或重新解压便携包即可，用户资料保存在用户数据目录不受影响。安装向导会清理旧布局的根目录文件、`.formrecall-updates` 与 `runtime/versions`，升级后同样只有启动入口和 `runtime`。

发布版本通过 Go 链接参数写入桌面程序，并记录在 `package-manifest.json`。`FormRecall.exe --version-json` 输出桌面版本与源码提交，供成品检查使用。引擎组件与兼容入口保留各自版本。发布说明优先读取 `.release-notes/v{version}.md`，其余版本使用 GitHub 自动生成说明。

构建失败可在同一 Actions run 中重跑失败任务。上传失败时保留 Release 草稿以便重试，上传完成后才公开。已有标签会固定到原提交，已公开的版本不会被覆盖。`push` 到 `v*` 标签也可触发同一流程。发布操作使用仓库提供的 `GITHUB_TOKEN`，无需另配 PAT。

本地生成同样命名的版本产物可执行以下命令。

```powershell
./scripts/package-native.ps1 -Version 0.2.0 `
  -OcctManifest 'temp/build-cache/occt-runtime/runtime-manifest.json' `
  -OutputDirectory 'temp/outputs/release-local' `
  -OutputManifest 'temp/outputs/release-local/release.json'
$release = Get-Content 'temp/outputs/release-local/release.json' -Raw | ConvertFrom-Json
./scripts/verify-native-release-startup.ps1 -PackageDirectory $release.package_directory -Version 0.2.0
```

`release.json` 的 schema 2 列出包目录、安装包、便携包、校验文件、版本与提交。输出目录中的同名产物已存在时脚本报错，应指定新的目录保存下一次构建。成品启动检查使用仓库外的独立用户数据和配置，并核对程序正常退出。发布工作流中的验证记录随 Actions artifact 保存 14 天。[准备发布](../../.github/workflows/prepare-release.yml) · [合并后发布](../../.github/workflows/publish-release-pr.yml) · [Windows 构建](../../.github/workflows/windows-release.yml)
