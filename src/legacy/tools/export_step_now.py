# -*- coding: utf-8 -*-
"""生成并运行 CATIA 批量导出 STEP 的 VBS（复用项目里已验证的 v4 宏）。"""
import io, os, re, subprocess, sys, time
sys.path.insert(0, ".")
import importlib.util
spec = importlib.util.spec_from_file_location("mkm", r"tools\make_catia_export_macro.py")
mkm = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mkm)
except SystemExit:
    pass
vbs_text = mkm.VBS
tmp = os.path.join(os.environ["TEMP"], "catia_step_out2")
os.makedirs(tmp, exist_ok=True)
vbs = os.path.join(os.environ["TEMP"], "catia_export_step_run.vbs")
io.open(vbs, "w", encoding="gbk", errors="replace").write(vbs_text)
print("VBS 已写出:", vbs, len(vbs_text), "字符")
t0 = time.time()
p = subprocess.run(["cscript", "//nologo", vbs,
                    r"C:\Users\25643\Desktop\零件参数", tmp],
                   capture_output=True, timeout=7200)
print("returncode", p.returncode)
print("stdout:", p.stdout.decode("gbk", "replace")[:400])
print("stderr:", p.stderr.decode("gbk", "replace")[:400])
n = sum(1 for dp, _dn, fns in os.walk(tmp) for f in fns if f.lower().endswith((".stp", ".step")))
print("导出 STEP 数量:", n, "用时 %.0fs" % (time.time()-t0))
