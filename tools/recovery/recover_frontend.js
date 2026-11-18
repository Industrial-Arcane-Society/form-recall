// 从 Tauri 的 exe 里把内嵌的前端资源抠出来。
//
// 背景：AI Modeler 是 Tauri(Rust)+Vue3 打的成品壳，Vue 源码不在本机，
// 但 Tauri 会把 vite build 出来的 dist 压进 exe。所以要改它的外观，只能
// 先把 dist 抠出来看。这个脚本就是那把螺丝刀。
//
// 为什么是 brotli：exe 里搜不到 zstd 魔数（28 b5 2f fd，计数 0），而资源
// 路径是明文、数据是无魔数的压缩流——Tauri 默认用 brotli。数据块和路径表
// 在资源段里是分开排的，所以做法是：定位明文路径，在它前后几 KB 里逐字节
// 试 brotli 解压，能解出合法 html/js/css 的那个偏移就是数据起点。
// 解压失败极快，暴力扫是划算的。
//
// 用法：
//   node recover_frontend.js
//   node recover_frontend.js --exe "D:\\apps\\ai-modeler.exe" --out ./recovered/assets
//
// 输出：<out>/ 下若干 index.html / index.js / index.css / viewport.js / viewport.css
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const EXE = arg("exe", "C:\\Program Files\\AI Modeler\\ai-modeler.exe");
const OUT = path.resolve(arg("out", path.join(__dirname, "recovered", "assets")));

// 资源表里的相对路径 -> 落盘文件名。换版本时文件名哈希会变，这里列的是
// 实测命中的一套；跑不通就先不带 targets，手工 indexOf 一下 "/index.html"。
const TARGETS = [
  { key: "index.html", path: "/index.html" },
  { key: "index.js", path: "/assets/index-CdCqM4ay.js" },
  { key: "index.css", path: "/assets/index-DX2Q7k2u.css" },
  { key: "viewport.js", path: "/assets/CadViewport-BBumYLp6.js" },
  { key: "viewport.css", path: "/assets/CadViewport-_iLJbu4o.css" },
];

const WINDOW = 4096;           // 在路径前后各扫这么多字节
const MAX_STREAM = 8 << 20;     // 单个资源解压后上限 8MB

if (!fs.existsSync(EXE)) {
  console.error(`找不到 exe：${EXE}\n用 --exe 指定实际路径。`);
  process.exit(1);
}

const data = fs.readFileSync(EXE);
fs.mkdirSync(OUT, { recursive: true });

function kindOf(p) {
  if (p.endsWith(".html")) return "html";
  if (p.endsWith(".css")) return "css";
  return "js";
}

function looksOk(buf, kind) {
  if (!buf || buf.length < 64) return false;
  const s = buf.toString("utf8");
  if (kind === "html") return /<!doctype|<html|<div id=/i.test(s);
  if (kind === "css") return /[.#@][\w-]+\s*\{/.test(s);
  return /function|=>|const |var |window\.|export/.test(s);
}

// 已用掉的偏移。不复用是必须的：相邻资源的数据块离得很近，
// 不排除的话第二份会直接把第一份的最长解再抄一遍——
// 实测 viewport.css 就这样拿到了 66853 字节的 index.css。
const used = new Set();

function scan(from, to, kind) {
  const hits = [];
  for (let off = from; off < to; off++) {
    if (used.has(off)) continue;
    const slice = data.subarray(off, Math.min(off + MAX_STREAM, data.length));
    try {
      const out = zlib.brotliDecompressSync(slice);
      if (looksOk(out, kind)) hits.push({ off, out });
    } catch (e) {
      // 绝大部分偏移都解不开，这是常态，不是错误
    }
  }
  return hits;
}

const report = [];
for (const t of TARGETS) {
  const p = data.indexOf(Buffer.from(t.path, "latin1"));
  if (p < 0) {
    report.push(`${t.key}: 路径未找到（exe 版本可能变了）`);
    continue;
  }
  const kind = kindOf(t.path);
  const hits = scan(Math.max(0, p - WINDOW), Math.min(data.length, p + WINDOW), kind);
  if (!hits.length) {
    report.push(`${t.key}: 未解出（路径 @${p}）`);
    continue;
  }
  hits.sort((a, b) => b.out.length - a.out.length);
  const best = hits[0];
  // 把它覆盖到的整段偏移都占上，防止后面的资源复用同一块数据
  for (let off = best.off; off < best.off + 64; off++) used.add(off);
  fs.writeFileSync(path.join(OUT, t.key), best.out);
  report.push(`${t.key}: ${best.out.length} 字节 <- 偏移 ${best.off}（候选 ${hits.length} 个）`);
}

console.log(`exe: ${EXE} (${(data.length / 1048576).toFixed(1)} MB)`);
console.log(`out: ${OUT}\n`);
console.log(report.join("\n"));
