// 统计打扁的 JS 里出现了哪些 Tauri IPC 关键字，并试着按 invoke("cmd") 直接取命令名。
//
// 实测结论：这台机器的 index.js 里 invoke 有 10 次，但 invoke("cad_xxx")
// 这种直呼写法是 0 条——命令名走的是变量/常量间接传入，所以这个探针只能
// 给出「IPC 形态」的判断，命令清单要靠 probe_commands.js 按前缀扫。
//
// 用法：node probe_index.js [--assets <dir>]
const fs = require("fs");
const path = require("path");

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const DIR = path.resolve(arg("assets", path.join(__dirname, "recovered", "assets")));
const file = path.join(DIR, "index.js");

if (!fs.existsSync(file)) {
  console.error(`找不到 ${file}\n先跑 recover_frontend.js。`);
  process.exit(1);
}

const js = fs.readFileSync(file, "utf8");
console.log("index.js 长度:", js.length);

for (const kw of ["__TAURI__", "__TAURI_INTERNALS__", "invoke", "listen(", "tauri://", "getCurrentWindow"]) {
  let c = 0, i = 0, first = -1;
  while ((i = js.indexOf(kw, i)) >= 0) { c++; if (first < 0) first = i; i += kw.length; }
  console.log(kw, "出现", c, "次, 首次 @", first);
}

// 只有命令名是字面量时才抓得到；抓到 0 条不代表没有命令，见文件头注释。
const re = /invoke\(["'`]([a-zA-Z0-9_:.-]+)["'`]/g;
const cmds = new Map();
let m;
while ((m = re.exec(js))) cmds.set(m[1], (cmds.get(m[1]) || 0) + 1);
console.log("\n通过字面量 invoke 调用的命令（共 " + cmds.size + " 个）:");
console.log([...cmds.entries()].map(([k, v]) => k + " x" + v).join("\n") || "  （无）");
