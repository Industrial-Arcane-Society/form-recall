# 从成品 exe 反解前端

> 用途：AI Modeler 是 Tauri(Rust) + Vue3 打包的产物，**Vue 源码不在本机**。
> 想在它的视觉基础上改界面（换成我们自己的 tkinter 版），只能先把 vite 打进
> exe 的 dist 抠出来，从 CSS 和打包产物里读出设计系统。

这套脚本只读取你自己机器上已安装的 exe，不联网、不改动原程序。

## 怎么跑

```bash
# 1) 抠出前端资源（默认读 C:\Program Files\AI Modeler\ai-modeler.exe）
node recover_frontend.js
node recover_frontend.js --exe "D:\apps\ai-modeler.exe" --out ./recovered/assets

# 2) 分析（默认读 ./recovered/assets）
node probe_css.js        # 设计 token：配色、字号、圆角、259 个类名
node probe_commands.js   # 按前缀扫 Tauri 命令名
node probe_internals.js  # IPC 封装形态（判断 Tauri v1 / v2）
node probe_index.js      # IPC 关键字统计
node probe_texts.js      # 中文文案（注意见下）
```

零依赖，只要 Node 自带模块（`fs` / `path` / `zlib`）。

## 实际拿到的结果（AI Modeler 0.1.2，exe 24.9 MB）

| 资源 | 大小 | 说明 |
| --- | --- | --- |
| `index.html` | 639 B | 完整，`theme-color #edf1f4`，标题「AI Modeler · 参数化建模工作台」 |
| `index.js` | 4.9 MB | 主 bundle，含 22 个 Tauri 命令名 |
| `index.css` | 67 KB | **23 个 CSS 变量 + 259 个类名**，皮肤就是照它抄的 |
| `viewport.js` | 5.6 MB | 三维视图那一块 |
| `viewport.css` | — | 见下面的已知问题 |

设计系统还原结果：主色 `#416b91`、背景 `#eef2f6`、卡片白 `#fff`、卡片圆角
`12px`、控件圆角 `10px`、字号阶梯 12px(120 处) / 14px(31) / 13px(18)。
落在 `ai_modeler/ui/theme.py` 的 `MODELER` 调色板里。

22 个 Tauri 命令（`index.js` 与 `viewport.js` 命中同一套，说明命令表定义在共享
chunk）：`cad_add_face_recess`、`cad_add_feature`、`cad_add_hole_array`、
`cad_apply_recipe`、`cad_attach_external_fact_evidence`、`cad_create_model`、
`cad_finish_model`、`cad_get_feature_spec`、`cad_get_reference_image`、
`cad_get_state`、`cad_inspect_references`、`cad_preview_model`、
`cad_record_visual_review`、`cad_remove_feature`、`cad_require_external_fact`、
`cad_restore_best_candidate`、`cad_review_reference_similarity`、
`cad_search_knowledge`、`cad_search_recipes`、`cad_set_construction_milestone`、
`cad_update_feature`、`cad_validate_model`。

IPC 是 Tauri **v2**：`window.__TAURI_INTERNALS__.invoke(cmd, args, opts)`。

## 已知问题与坑

1. **`viewport.css` 抠错，已修**。相邻资源的数据块离得近，早期版本不排除已用
   偏移，`viewport.css` 会把 `index.css` 的最长解再抄一遍（拿到 66853 字节的
   同一份内容）。现在 `recover_frontend.js` 用 `used` 集合占位，改完不再复用。
2. **命令名不是 `invoke("cmd")` 直呼**。`probe_index.js` 按字面量正则会得到
   0 条，这不是没命令——命令名走变量间接传入。要用 `probe_commands.js`。
3. **资源文件名带哈希**。换版本后 `index-CdCqM4ay.js` 这类名字会变，脚本里的
   `TARGETS` 会全部「路径未找到」。改法：先在 exe 里搜 `/index.html` 定位资源
   表，再按实际哈希更新 `TARGETS`。
4. **`probe_texts.js` 掏出的中文多半不是 UI 文案**。实测 2846 条里大部分是
   Rust 侧的几何校验/迁移提示，当对照用，别当界面文本清单。
5. **Brotli 无魔数**。不能靠 magic bytes 找压缩块，只能按明文路径定位后逐字节
   试解。exe 里 zstd 帧计数为 0，所以排除 zstd。

## 边界

- 解出来的 `index.js` / `index.css` 是**原作者的产**物，只用于分析外观与接口，
  不要直接分发。
- 这套方法针对 Tauri v2 + brotli 的默认配置。别的打包方式（例如用 asar、
  或前端资源走外部目录）不适用。
