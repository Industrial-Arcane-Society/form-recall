// 按前缀扫出候选 Tauri 命令名。
//
// 上一版探针（probe_index.js）发现命令名不是 invoke("cmd") 直呼，但它们是
// 明文，而且成串挨着 IPC 封装函数出现。所以换个思路：在整份 bundle 里按
// 常见动词前缀搜 "snake_case" 字面量，按出现次数排序。
//
// 用法：node probe_commands.js [--assets <dir>]
const fs = require("fs");
const path = require("path");

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const DIR = path.resolve(arg("assets", path.join(__dirname, "recovered", "assets")));

const PREFIXES = [
  "cad_", "export_", "import_", "open_", "save_", "load_", "list_", "get_",
  "set_", "create_", "delete_", "resolve_", "run_", "start_", "stop_",
  "scan_", "read_", "write_", "ping", "quit", "apply_", "build_", "clear_",
];

function scan(file) {
  const js = fs.readFileSync(path.join(DIR, file), "utf8");
  const found = new Map(); // name -> [positions]
  for (const pre of PREFIXES) {
    const re = new RegExp('"(' + pre + '[a-z0-9_]{2,48})"', "g");
    let m;
    while ((m = re.exec(js))) {
      const k = m[1];
      if (!found.has(k)) found.set(k, []);
      found.get(k).push(m.index);
    }
  }
  const names = [...found.keys()].sort();
  console.log("\n########## " + file + " ##########");
  console.log("候选字符串 " + names.length + " 个");

  // 带下划线的才像命令；纯单词的多半是 UI 状态键
  const cmds = names.filter((n) => n.includes("_"));
  console.log("\n-- 全量（" + cmds.length + "）--");
  for (const c of cmds) console.log("  " + c + "  x" + found.get(c).length);
}

for (const f of ["index.js", "viewport.js"]) {
  try { scan(f); } catch (e) { console.log(f + " 读取失败: " + e.message); }
}
