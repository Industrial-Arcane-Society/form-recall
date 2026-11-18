// 把界面中文文案掏出来——这是最快的 UI 地图。
//
// 打扁的 bundle 里模板字符串都还在，只是散落各处。中文不会被打包器改名，
// 所以按「连续中文（可夹字母数字和常见标点）」切片，去重后按长度排序：
// 长的基本是标题/按钮/提示文案，短的多半是单位或占位符。
//
// 注意：AI Modeler 里这套能掏出 2846 条，但多数是 Rust 侧的几何校验与迁移
// 提示（例如「锥齿轮包含低于 OCCT 缝合容差的离散三角面」），不是 UI 标签。
// 所以这份输出用来对照，不要当成界面文案清单。
//
// 用法：node probe_texts.js [--assets <dir>] [--file index.js]
const fs = require("fs");
const path = require("path");

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const DIR = path.resolve(arg("assets", path.join(__dirname, "recovered", "assets")));
const NAME = arg("file", "index.js");
const full = path.join(DIR, NAME);

if (!fs.existsSync(full)) {
  console.error(`找不到 ${full}\n先跑 recover_frontend.js。`);
  process.exit(1);
}

function extract(file) {
  const js = fs.readFileSync(file, "utf8");
  const out = new Map();
  // 允许中文之间夹字母数字和常见符号，避免把「间隙 5 mm」切成两段
  const re = /[\u4e00-\u9fa5][\u4e00-\u9fa5\u3000-\u303fA-Za-z0-9\s\-×·、，。：；！？（）《》「」“”%./+~=<>]*/g;
  let m;
  while ((m = re.exec(js))) {
    const s = m[0].trim();
    if (s.length < 2 || s.length > 60) continue;
    out.set(s, (out.get(s) || 0) + 1);
  }
  return out;
}

const map = extract(full);
const items = [...map.entries()];
console.log("########## " + NAME + "：中文串 " + items.length + " 条 ##########");

const long = items.filter(([s]) => s.length >= 4).sort((a, b) => b[0].length - a[0].length);
console.log("\n===== 长文案（>=4 字，共 " + long.length + "）=====");
for (const [s, n] of long) console.log("  " + s + (n > 1 ? "   x" + n : ""));
