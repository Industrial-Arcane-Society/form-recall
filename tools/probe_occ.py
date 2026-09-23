# -*- coding: utf-8 -*-
"""OpenCASCADE 能力实测（修正版：OCCT 的 MakeBox 角点在原点，刀具体要放进盒子里）。"""
import math, time
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
def vol(s):
    p = GProp_GProps(); BRepGProp.VolumeProperties_s(s, p); return p.Mass()
def lat(shape, tol=0.1):
    BRepMesh_IncrementalMesh(shape, tol, False, 0.5, True)
    n = 0; ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        cur = ex.Current()
        f = TopoDS.Face_s(cur) if hasattr(TopoDS, "Face_s") else TopoDS.Face(cur)
        loc = TopLoc_Location(); tr = BRep_Tool.Triangulation_s(f, loc)
        if tr is not None: n += tr.NbTriangles()
        ex.Next()
    return n
def cut(shape, cutter):
    op = BRepAlgoAPI_Cut(shape, cutter); op.Build(); return op.Shape()
box = BRepPrimAPI_MakeBox(gp_Pnt(-60, -60, 0), 120, 120, 20).Shape()
A = 0.5*24*2.5**2*math.sin(2*math.pi/24)
print("空箱体积 %.1f（应 288000）" % vol(box))
for N in (16, 48, 96, 192, 384):
    shape = box; k = 0; pos = set()
    t0 = time.time()
    for i in range(N):
        x = -45 + 15*(i % 7); y = -45 + 15*((i//7) % 7)
        if (x, y) in pos: continue
        pos.add((x, y))
        c = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, -10.0), gp_Dir(0,0,1)), 2.5, 40.0).Shape()
        shape = cut(shape, c); k += 1
    dt = time.time()-t0
    exp = 288000 - k*A*20
    v = vol(shape)
    print("箱体 + %3d 孔: %6.2fs  体积 %10.2f  解析 %10.2f  偏差 %7.4f%%  三角面 %d"
          % (k, dt, v, exp, 100*abs(v-exp)/exp, lat(shape)))
