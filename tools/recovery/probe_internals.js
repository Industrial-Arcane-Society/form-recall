// 打印 __TAURI_INTERNALS__ 前后的上下文，看清 IPC 封装长什么样。
//
// 这是判断「是 Tauri v1 还是 v2」以及「invoke 被封在哪个函数里」的最快办法。
// 实测 AI Modeler 用的是 v2：window.__TAURI_INTERNALS__.invoke(cmd, args, opts)，
// __TAURI__（v1 的全局对象）出现 0 次。
//
// 用法：node probe_internals.js [--assets <dir>]
const fs = require("fs");
const path = require("path");

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const DIR = path.resolve(arg("assets", path.join(__dirname, "recovered", "assets")));

function show(file) {
  const full = path.join(DIR, file);
  if (!fs.existsSync(full)) {
    console.log(`${file} 不存在，跳过`);
    return;
  }
  const js = fs.readFileSync(full, "utf8");
  console.log("\n########## " + file + " (" + js.length + " 字符) ##########");

  const marks = [];
  let i = 0;
  while ((i = js.indexOf("__TAURI_INTERNALS__", i)) >= 0) {
    marks.push(i);
    i += 19;
  }
  console.log("__TAURI_INTERNALS__ 出现 " + marks.length + " 次");
  for (const m of marks.slice(0, 6)) {
    console.log("  ..." + js.slice(Math.max(0, m - 120), m + 260).replace(/\s+/g, " ") + "...");
  }
}

show("index.js");
