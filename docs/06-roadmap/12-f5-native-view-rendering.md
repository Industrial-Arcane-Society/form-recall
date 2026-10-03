# F5.1：原生单视图渲染

## 范围

Rust 从 OCCT 三角网格生成独立 PNG：front、back、right、left、top、bottom、iso。渲染不调用 Python、浏览器或 LLM。它是后续视觉工具和导出接口的基础，本项完成不代表 Agent 图片复刻或 CATIA 验收完成。

## 实现

- CPU z-buffer 遮挡，有限网格及资源上限检查。
- front 从 +Y 看向中心，横向 X、纵向 Z；right 从 +X；top 从 +Z。
- 七视图共用三维包围盒对角线确定的单位比例，不独立拉伸各个视图。
- 返回 PNG 及视角、投影、中心、单位比例、尺寸和深度/光照元数据。
- 可关闭的真实深度突变/轮廓边界显示，不绘制全部三角片边线。正交正视图仍不能独自证明凹槽深度，须结合侧视图和等轴测图。

## 验收

在 `src/apps/desktop/src-tauri/` 执行：

```powershell
cargo test native_render::tests --lib
cargo run --example native_render_probe -- target/f4-occt/runtime-manifest.json target/f5-native-render
```

实际固定 OCCT runtime 构造测试实体、凹槽及九孔，1000 个三角形，生成七张 PNG。五项定向测试核对视角坐标、共享比例、实际远近三角形遮挡、深度边界与编码、非法网格拒绝。测试工件来自确定性几何 fixture，并非无上下文 Agent 对用户图片的复刻。

## 后续

尚需生产预览接线、输出版本与 SHA 清单、本地 AIMODEL/STL 兼容性、Quick 路由及真实 DSH 多模态对比。安装包尚未包含原生 OCCT runtime。
