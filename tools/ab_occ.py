# -*- coding: utf-8 -*-
"""OCC 后端 vs BSP 内核：在真的零件上对比（时间/体积/孔数）。"""
import sys, time, io, json
sys.path.insert(0, ".")
from ai_modeler import partlib as PL, docmodel as DM, kernel as K, occ_kernel as OC
from ai_modeler import nlparser as NLP
def build_both(prompt):
    r = PL.build_features_ex(prompt)
    if not r:
        return None
    fs = r[1]
    doc1 = DM.Document()
    for f in fs: doc1.add(f)
    t0 = time.time()
    try:
        body = doc1.build(max_faces=400000)
        t_bsp = time.time() - t0; v_bsp = K.volume_of(body); n_bsp = len(body)
    except Exception as e:
        t_bsp, v_bsp, n_bsp = -1, 0, 0
    doc2 = DM.Document()
    for f in fs: doc2.add(f)
    t0 = time.time()
    try:
        shp = OC.build_document(doc2)
        t_occ = time.time() - t0; v_occ = OC.volume(shp)
        polys = OC.shape_to_polys(shp); n_occ = len(polys)
    except Exception as e:
        print("   OCC 失败:", type(e).__name__, str(e)[:90])
        t_occ, v_occ, n_occ = -1, 0, 0
    return dict(prompt=prompt, t_bsp=t_bsp, v_bsp=v_bsp, n_bsp=n_bsp,
                t_occ=t_occ, v_occ=v_occ, n_occ=n_occ)
for p in ("EGT型材 EGT001 SW80x088", "EGT型材 EGT006 D30x088", "EGT型材 EGT006 D30x373",
          "销座 KMS-M02-H03-20-RD", "支座 KMS-M05-010-100-D", "内六角螺栓 M16x60"):
    r = build_both(p)
    if r is None:
        print("%-30s 建不出来" % p); continue
    dv = 100*abs(r["v_occ"]-r["v_bsp"])/max(r["v_bsp"], 1e-9) if r["v_occ"] and r["v_bsp"] else -1
    print("%-28s BSP %7.2fs V=%12.1f(%6d面) | OCC %7.2fs V=%12.1f(%6d面) 体积差 %6.3f%%" %
          (p[:28], r["t_bsp"], r["v_bsp"], r["n_bsp"], r["t_occ"], r["v_occ"], r["n_occ"], dv))
