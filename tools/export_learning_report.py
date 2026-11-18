# -*- coding: utf-8 -*-
"""生成「元增量学习观察面板」——一个双击就能看的自包含 HTML。

为什么不改现有界面：
    tkinter 版的面板只有那个程序能看，AI Modeler 的前端我们没有源码也改不了。
    学习状态这件事，最通用的查看方式是浏览器——谁都能打开，也能直接截图发人。

数据来源（都是真数据，不编）：
    kb/meta_memory.json     零件族记忆（19 族 / 1816 条已验证样本）
    datasets/*.csv          企业工序库（16 道工序 / 五阶段）
    kb/process_memory.json  工序执行统计（存在才读，没有就显示「未开始执行」）

用法：
    python tools/export_learning_report.py
    生成后自动用默认浏览器打开。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_modeler.meta import DEFAULT_STORE, MetaMemory, ProcessMemory  # noqa: E402
from ai_modeler.workflow import load_process, load_scenes  # noqa: E402

OUT_REPO = ROOT / "reports" / "元增量学习观察面板.html"
OUT_DESKTOP = Path(os.path.expanduser("~")) / "Desktop" / "元增量学习观察面板.html"


def collect() -> dict:
    """把两层记忆的真实状态读出来。任何一层缺失都不让整个面板崩掉。"""
    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "families": [],
        "family_stats": {},
        "steps": [],
        "process_stats": {},
        "process_hot_failures": [],
        "warnings": [],
    }

    # ---- 零件族记忆 ----
    try:
        mem = MetaMemory(str(ROOT / DEFAULT_STORE))
        try:
            mem.load()
        except Exception:
            pass
        data["family_stats"] = mem.stats()
        for row in mem.families():
            data["families"].append({
                "name": row.get("name", ""),
                "n_samples": int(row.get("n_samples") or 0),
                "n_verified": int(row.get("n_verified") or 0),
                "n_candidates": int(row.get("n_candidates") or 0),
            })
        data["families"].sort(key=lambda r: -r["n_samples"])
    except Exception as exc:
        data["warnings"].append("零件族记忆读不出来：%s" % exc)

    # ---- 企业工序库 + 工序记忆 ----
    try:
        lib = load_process()
        pm = ProcessMemory(str(ROOT / "kb" / "process_memory.json"))
        pm.learn_steps([
            {"step_id": s.step_id, "stage": s.stage,
             "text": s.search_text(), "status": s.status}
            for s in lib.steps
        ], scenes=load_scenes())
        stat_by_id = {r.step_id: r for r in pm.records.values()}
        for step in lib.steps:
            rec = stat_by_id.get(step.step_id)
            trials = rec.trials if rec else 0
            success = rec.success if rec else 0
            data["steps"].append({
                "step_id": step.step_id,
                "stage": step.stage,
                "stage_no": step.stage_no,
                "action": step.action,
                "criterion": step.criterion,
                "status": step.status,
                "trials": trials,
                "success": success,
                "success_rate": (None if trials == 0 else round(success / trials, 3)),
                "fail_hints": (rec.fail_notes[-3:] if rec else []),
            })
        data["process_stats"] = pm.stats()
        data["process_hot_failures"] = pm.hot_failures(6)
    except Exception as exc:
        data["warnings"].append("工序库读不出来：%s" % exc)

    return data


HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>元增量学习 · 观察面板</title>
<style>
:root{
  --bg:#0f1319; --panel:#171d26; --line:#26303d; --text:#e6edf5; --dim:#8b9bb0;
  --accent:#3fa9f5; --ok:#3fb950; --warn:#d29922; --err:#f85149; --mute:#5c6b7f;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:14px/1.6 "Microsoft YaHei","Segoe UI",sans-serif;padding:24px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--dim);font-size:13px;margin-bottom:20px}
.wrap{max-width:1180px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{color:var(--dim);font-size:12px;margin-bottom:6px}
.card .v{font-size:26px;font-weight:700;letter-spacing:.5px}
.card .v small{font-size:13px;font-weight:400;color:var(--dim);margin-left:4px}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:18px;margin-bottom:18px}
h2{font-size:15px;margin:0 0 14px;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:3px;height:14px;background:var(--accent);border-radius:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500;font-size:12px}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:6px;background:#202834;border-radius:3px;overflow:hidden;min-width:80px}
.bar>i{display:block;height:100%;background:var(--accent);border-radius:3px}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;
  border:1px solid;line-height:1.5;white-space:nowrap}
.b-ok{color:var(--ok);border-color:#1f4d2c;background:#12251a}
.b-warn{color:var(--warn);border-color:#4d3d12;background:#241f10}
.b-mute{color:var(--mute);border-color:#2a3440;background:#161c24}
.b-accent{color:var(--accent);border-color:#1b3f5c;background:#10202e}
.empty{color:var(--dim);font-size:13px;padding:14px 0}
.hint{color:var(--dim);font-size:12px;margin-top:10px;line-height:1.7}
.warn{background:#241a10;border:1px solid #4d3d12;color:#e3b341;padding:10px 14px;
  border-radius:8px;font-size:13px;margin-bottom:16px}
.foot{color:var(--mute);font-size:12px;text-align:center;margin-top:26px;line-height:1.9}
code{background:#202834;padding:1px 5px;border-radius:4px;font-size:12px;color:#a8c7e8}
</style>
</head>
<body>
<div class="wrap">
  <h1>元增量学习 · 观察面板</h1>
  <div class="sub">生成于 __GENERATED_AT__ ｜ 数据来自本机记忆库，不是示例值</div>
  <div id="warnbox"></div>

  <div class="cards" id="cards"></div>

  <section>
    <h2>零件族记忆 <span class="badge b-accent" id="fam-note"></span></h2>
    <div id="famtable"></div>
    <div class="hint">
      这一层记的是「这个零件属于哪一族」：每条已验证样本都会并进该族的原型，
      下次遇到同类零件直接命中。样本越多，原型越稳。
    </div>
  </section>

  <section>
    <h2>企业工序记忆 <span class="badge b-accent" id="proc-note"></span></h2>
    <div id="proctable"></div>
    <div class="hint" id="prochint"></div>
  </section>

  <section>
    <h2>常见失败原因</h2>
    <div id="fails"></div>
  </section>

  <div class="foot">
    记忆库：<code>kb/meta_memory.json</code> · <code>kb/process_memory.json</code><br>
    重新生成：<code>python tools/export_learning_report.py</code>
  </div>
</div>

<script>
const DATA = __DATA__;

function el(tag, cls, text){
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function renderCards(){
  const fs = DATA.family_stats || {};
  const ps = DATA.process_stats || {};
  const cards = [
    ['已学零件族', fs.families ?? 0, '个'],
    ['已验证样本', (fs.samples ?? 0).toLocaleString(), '条'],
    ['工序总数', (ps.steps ?? DATA.steps.length), '道'],
    ['已执行工序', ps.attempted_steps ?? 0, '道'],
    ['累计执行', ps.trials ?? 0, '次'],
    ['执行成功率', ps.success_rate == null ? '—' : Math.round(ps.success_rate*100)+'%', ''],
  ];
  const box = document.getElementById('cards');
  cards.forEach(([k,v,u])=>{
    const c = el('div','card');
    c.appendChild(el('div','k',k));
    const vv = el('div','v', String(v));
    if (u) vv.appendChild(el('small', null, u));
    c.appendChild(vv);
    box.appendChild(c);
  });
}

function renderFamilies(){
  const rows = DATA.families || [];
  document.getElementById('fam-note').textContent = rows.length + ' 个族';
  const box = document.getElementById('famtable');
  if (!rows.length) { box.appendChild(el('div','empty','还没有学到任何零件族。')); return; }
  const max = Math.max(...rows.map(r=>r.n_samples), 1);
  const t = el('table');
  const head = el('tr');
  [['零件族',''],['已验证','num'],['候选','num'],['样本占比',''],['原型状态','']].forEach(([h,c])=>{
    head.appendChild(el('th', c, h));
  });
  t.appendChild(head);
  rows.forEach(r=>{
    const tr = el('tr');
    tr.appendChild(el('td', null, r.name));
    tr.appendChild(el('td','num', r.n_verified.toLocaleString()));
    tr.appendChild(el('td','num', String(r.n_candidates)));
    const td = el('td'); const bar = el('div','bar');
    const fill = el('i'); fill.style.width = Math.round(100*r.n_samples/max) + '%';
    bar.appendChild(fill); td.appendChild(bar); tr.appendChild(td);
    const s = el('td');
    const ok = r.n_samples > 0;
    s.appendChild(el('span','badge ' + (ok?'b-ok':'b-mute'), ok?'已建立原型':'待积累'));
    tr.appendChild(s);
    t.appendChild(tr);
  });
  box.appendChild(t);
}

const STAGE_LABEL = {1:'工程输入',2:'单元规划',3:'参数建模',4:'关联装配',5:'校核输出'};
const CONF_CLASS = {'视频证实':'b-ok','报告推断':'b-warn','待企业确认':'b-mute'};

function renderSteps(){
  const rows = DATA.steps || [];
  document.getElementById('proc-note').textContent = rows.length + ' 道工序';
  const box = document.getElementById('proctable');
  if (!rows.length) { box.appendChild(el('div','empty','工序库还没导入。')); return; }
  const t = el('table');
  const head = el('tr');
  [['工序',''],['阶段',''],['操作',''],['证据状态',''],['执行','num'],['成功率','']].forEach(([h,c])=>{
    head.appendChild(el('th', c, h));
  });
  t.appendChild(head);
  rows.forEach(r=>{
    const tr = el('tr');
    tr.appendChild(el('td', null, r.step_id));
    tr.appendChild(el('td', null, STAGE_LABEL[r.stage_no] || r.stage));
    tr.appendChild(el('td', null, r.action.length > 34 ? r.action.slice(0,34)+'…' : r.action));
    const st = el('td');
    st.appendChild(el('span','badge ' + (CONF_CLASS[r.status]||'b-mute'), r.status));
    tr.appendChild(st);
    tr.appendChild(el('td','num', String(r.trials)));
    const sr = el('td');
    if (r.success_rate == null) {
      sr.appendChild(el('span','badge b-mute','未执行'));
    } else {
      const bar = el('div','bar'); const f = el('i');
      f.style.width = Math.round(100*r.success_rate)+'%';
      f.style.background = r.success_rate >= .8 ? 'var(--ok)' : (r.success_rate >= .5 ? 'var(--warn)' : 'var(--err)');
      bar.appendChild(f); sr.appendChild(bar);
    }
    tr.appendChild(sr);
    t.appendChild(tr);
  });
  box.appendChild(t);
  document.getElementById('prochint').innerHTML =
    '这一层记的是「这类任务该走哪道工序、这道工序历史上栽在哪」。' +
    '当前 <b>' + (DATA.process_stats.attempted_steps || 0) + '</b> 道工序有执行记录；' +
    '走完一次五步流程并调用 <code>process_record</code> 之后，成功率与失败原因才会出现。';
}

function renderFails(){
  const box = document.getElementById('fails');
  const rows = DATA.process_hot_failures || [];
  if (!rows.length) {
    box.appendChild(el('div','empty','还没有失败记录。这既是好事，也可能只是还没开始执行。'));
    return;
  }
  const t = el('table');
  const head = el('tr');
  head.appendChild(el('th',null,'失败原因')); head.appendChild(el('th','num','出现次数'));
  t.appendChild(head);
  rows.forEach(r=>{
    const tr = el('tr');
    tr.appendChild(el('td',null,r.reason));
    tr.appendChild(el('td','num',String(r.count)));
    t.appendChild(tr);
  });
  box.appendChild(t);
}

function renderWarn(){
  const list = DATA.warnings || [];
  if (!list.length) return;
  const box = document.getElementById('warnbox');
  const d = el('div','warn', '部分数据没读到：' + list.join('；'));
  box.appendChild(d);
}

renderWarn(); renderCards(); renderFamilies(); renderSteps(); renderFails();
</script>
</body>
</html>
"""


def build_html(data: dict) -> str:
    return (HTML
            .replace("__GENERATED_AT__", data["generated_at"])
            .replace("__DATA__", json.dumps(data, ensure_ascii=False)))


def main() -> int:
    data = collect()
    html = build_html(data)
    for target in (OUT_REPO, OUT_DESKTOP):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        print("已生成：%s（%.1f KB）" % (target, target.stat().st_size / 1024))

    print("\n=== 面板内容概览 ===")
    print("  零件族        : %d 个" % len(data["families"]))
    print("  已验证样本    : %s 条" % (data["family_stats"].get("samples") or 0))
    print("  工序          : %d 道（已执行 %s 道）"
          % (len(data["steps"]), data["process_stats"].get("attempted_steps") or 0))
    for w in data["warnings"]:
        print("  [警告] %s" % w)

    try:
        webbrowser.open(OUT_DESKTOP.as_uri())
        print("\n已在浏览器打开。")
    except Exception as exc:
        print("\n自动打开失败（不影响文件）：%s" % exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
