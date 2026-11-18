// 从打扁的 CSS 里还原设计系统：配色、字号阶梯、圆角、类名骨架。
//
// 前端源码没了，但 CSS 是「设计决定的化石」——颜色变量、字号、圆角都还在，
// 照着能把外观还原个八九成。这个脚本只统计，不猜。
//
// 我们用它拿到的实际结果（AI Modeler 0.1.2）：23 个 CSS 变量、259 个类名、
// 主色 #416b91、卡片圆角 12px、控件圆角 10px。tkinter 皮肤就是照这份数据
// 抄的，见 ai_modeler/ui/theme.py 的 MODELER 调色板。
//
// 用法：node probe_css.js [--assets <dir>] [--file index.css]
const fs = require("fs");
const path = require("path");

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const DIR = path.resolve(arg("assets", path.join(__dirname, "recovered", "assets")));
const NAME = arg("file", "index.css");
const full = path.join(DIR, NAME);

if (!fs.existsSync(full)) {
  console.error(`找不到 ${full}\n先跑 recover_frontend.js。`);
  process.exit(1);
}

const css = fs.readFileSync(full, "utf8");
console.log(`${NAME} 长度 ${css.length} 字节\n`);

// 1) CSS 自定义属性（设计 token 一般都在这里）
const vars = [...css.matchAll(/(--[a-zA-Z0-9-]+)\s*:\s*([^;}]{1,80})/g)];
const varMap = new Map();
for (const [, k, v] of vars) if (!varMap.has(k)) varMap.set(k, v.trim());
console.log("===== CSS 变量 " + varMap.size + " 个 =====");
for (const [k, v] of varMap) console.log("  " + k + ": " + v);

// 2) 颜色频次（非变量的裸色值，能看出实际用色）
const colors = new Map();
for (const m of css.matchAll(/#[0-9a-fA-F]{3,8}\b/g)) {
  const c = m[0].toLowerCase();
  colors.set(c, (colors.get(c) || 0) + 1);
}
console.log("\n===== 十六进制色值 top 30 =====");
[...colors.entries()].sort((a, b) => b[1] - a[1]).slice(0, 30)
  .forEach(([c, n]) => console.log("  " + c + "  x" + n));

// 3) 字号阶梯
const sizes = new Map();
for (const m of css.matchAll(/font-size:\s*([0-9.]+(?:px|rem|em))/g)) {
  sizes.set(m[1], (sizes.get(m[1]) || 0) + 1);
}
console.log("\n===== 字号 =====");
[...sizes.entries()].sort((a, b) => b[1] - a[1]).forEach(([s, n]) => console.log("  " + s + "  x" + n));

// 4) 圆角
const radii = new Map();
for (const m of css.matchAll(/border-radius:\s*([^;}]{1,40})/g)) {
  const v = m[1].trim();
  radii.set(v, (radii.get(v) || 0) + 1);
}
console.log("\n===== 圆角 =====");
[...radii.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).forEach(([s, n]) => console.log("  " + s + "  x" + n));

// 5) 类名骨架（布局命名最能说明 UI 结构）
const classes = new Map();
for (const m of css.matchAll(/\.([a-zA-Z][a-zA-Z0-9_-]{2,40})/g)) {
  const c = m[1];
  classes.set(c, (classes.get(c) || 0) + 1);
}
const named = [...classes.keys()].sort();
console.log("\n===== 类名 " + named.length + " 个 =====");
console.log(named.join("  "));
