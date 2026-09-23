# -*- coding: utf-8 -*-
import json, os, time, urllib.request
OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_wheels"))
def fetch(pkg, ver=None, want="win_amd64"):
    with urllib.request.urlopen("https://pypi.org/pypi/%s/json" % pkg, timeout=40) as r:
        d = json.load(r)
    v = ver or d["info"]["version"]
    ws = [f for f in d["releases"].get(v, []) if f["filename"].endswith(".whl")
          and want in f["filename"]]
    if not ws:
        print("没有", pkg, v, want); return
    for f in ws:
        dst = os.path.join(OUT, f["filename"])
        if os.path.exists(dst):
            print("已有", f["filename"]); continue
        t0 = time.time()
        with urllib.request.urlopen(f["url"], timeout=120) as r2, open(dst, "wb") as fh:
            while True:
                c = r2.read(1024 * 512)
                if not c: break
                fh.write(c)
        print("下载 %-52s %6.1f MB  %.0fs" % (f["filename"], os.path.getsize(dst)/1e6, time.time()-t0))
fetch("vtk", ver="9.6.2")
with urllib.request.urlopen("https://pypi.org/pypi/vtk/9.6.2/json", timeout=40) as r:
    d = json.load(r)
print("vtk 依赖:", d["info"].get("requires_dist"))
