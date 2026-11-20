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

每次打包生成新的目录及 ZIP，包含 Go 桌面、Rust 核心进程、必要提示与知识资源、DSH 离线包、CATIA 离线依赖、OCCT DLL 和许可证。Go 依赖许可位于 `licenses/go`，SVG 图标完整许可位于 `licenses/icons/Lucide-LICENSE`。`package-manifest.json` 记录源码提交、工作区是否有未提交变更及所有包内文件 SHA-256。

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
