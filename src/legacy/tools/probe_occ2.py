# -*- coding: utf-8 -*-
"""OCC 一次布尔（把 264 个刀具体合成一个 compound）—— 长管 + 密孔的正确做法。"""
import math, time
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCP.TopoDS import TopoDS_Compound
from OCP.BRep import BRep_Builder
def vol(s):
    p = GProp_GProps(); BRepGProp.VolumeProperties_s(s, p); return p.Mass()
tube = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0,0,0), gp_Dir(1,0,0)), 15.0, 1000.0).Shape()
tube = BRepAlgoAPI_Cut(tube, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(-100,0,0), gp_Dir(1,0,0)), 12.5, 1200.0).Shape()).Shape()
print("空管体积 %.1f" % vol(tube))
# 264 个刀具体 → 一个 compound
comp = TopoDS_Compound(); b = BRep_Builder(); b.MakeCompound(comp)
n = 0
for i in range(66):
    x = 13 + 15*i
    for z in (-10.54, 0.0, 10.54):
        b.Add(comp, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, -30.0, z), gp_Dir(0,1,0)), 3.0, 60.0).Shape()); n += 1
    b.Add(comp, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, 0.0, -30.0), gp_Dir(0,0,1)), 3.0, 60.0).Shape()); n += 1
t0 = time.time()
res = BRepAlgoAPI_Cut(tube, comp); res.Build(); shape = res.Shape()
print("一次布尔切除 %d 个刀具体：用时 %.2fs  IsDone=%s  体积 %.1f（管 %.1f，去掉 %.1f）"
      % (n, time.time()-t0, res.IsDone(), vol(shape), vol(tube), vol(tube)-vol(shape)))
# 再试试"先把刀具体 fuse 成一根"再切
t0 = time.time()
f = BRepAlgoAPI_Fuse(comp, comp); f.Build()
print("（参考）compound fuse 用时 %.2fs" % (time.time()-t0))
