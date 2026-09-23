# -*- coding: utf-8 -*-
"""导出器: STL / OBJ / JSON 工程文件 / CATIA 自动化脚本。"""
from __future__ import annotations

import json
import os
import struct
import time
from typing import List

from . import kernel as K


def _safe_tris(polys):
    tris = K.polygons_to_triangles(polys)
    out = []
    for (a, b, c, _t) in tris:
        n = K.v_cross(K.v_sub(b, a), K.v_sub(c, a))
        if K.v_len(n) < 1e-12:
            continue
        out.append((a, b, c, K.v_norm(n)))
    return out


def export_stl(polys, path: str, name: str = 'part', binary: bool = True) -> str:
    tris = _safe_tris(polys)
    if binary:
        with open(path, 'wb') as f:
            f.write(('%-80s' % ('AI Modeler STL - ' + name))[:80].encode('ascii', 'replace'))
            f.write(struct.pack('<I', len(tris)))
            for (a, b, c, n) in tris:
                f.write(struct.pack('<3f', *n))
                f.write(struct.pack('<3f', *a))
                f.write(struct.pack('<3f', *b))
                f.write(struct.pack('<3f', *c))
                f.write(struct.pack('<H', 0))
    else:
        with open(path, 'w', encoding='utf-8') as f:
            f.write('solid %s\n' % name)
            for (a, b, c, n) in tris:
                f.write('  facet normal %.6e %.6e %.6e\n' % n)
                f.write('    outer loop\n')
                for v in (a, b, c):
                    f.write('      vertex %.6e %.6e %.6e\n' % v)
                f.write('    endloop\n  endfacet\n')
            f.write('endsolid %s\n' % name)
    return path


def export_obj(polys, path: str, name: str = 'part') -> str:
    verts: List = []
    index = {}
    lines = ['# AI Modeler OBJ export', '# %s' % time.strftime('%Y-%m-%d %H:%M:%S'),
             'o %s' % name]
    faces = []
    for (a, b, c, _n) in _safe_tris(polys):
        idx = []
        for v in (a, b, c):
            key = (round(v[0], 6), round(v[1], 6), round(v[2], 6))
            if key not in index:
                index[key] = len(verts) + 1
                verts.append(key)
            idx.append(index[key])
        faces.append('f %d %d %d' % tuple(idx))
    for v in verts:
        lines.append('v %.6f %.6f %.6f' % v)
    lines.extend(faces)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return path


def export_project(doc, path: str, extra: dict = None) -> str:
    d = doc.to_dict()
    d['exported_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    if extra:
        d.update(extra)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return path


def load_project(path: str):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def export_report(doc, path: str, prompt: str = '') -> str:
    s = doc.stats()
    bb = s['bbox']
    lines = []
    lines.append('AI 自动建模助手 —— 建模报告')
    lines.append('=' * 52)
    lines.append('零件名称 : %s' % doc.name)
    lines.append('生成时间 : %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    if prompt:
        lines.append('需求描述 : %s' % prompt)
    lines.append('')
    lines.append('特征树 (%d 项)' % len(doc.features))
    lines.append('-' * 52)
    for i, f in enumerate(doc.features, 1):
        flag = '  ' if f.enabled else '× '
        lines.append('%s%2d. [%s] %s' % (flag, i, '切除' if f.op == 'cut' else '合并', f.describe()))
    lines.append('')
    lines.append('几何统计')
    lines.append('-' * 52)
    lines.append('三角面片 : %d' % s['triangles'])
    if bb:
        lines.append('包围盒   : X[%.2f, %.2f]  Y[%.2f, %.2f]  Z[%.2f, %.2f] mm'
                     % (bb[0], bb[3], bb[1], bb[4], bb[2], bb[5]))
        lines.append('外形尺寸 : %.2f × %.2f × %.2f mm' % s['size'])
    lines.append('体积     : %.2f mm³ (%.2f cm³)' % (s['volume'], s['volume'] / 1000.0))
    lines.append('表面积   : %.2f mm²' % s['area'])
    lines.append('')
    lines.append('构建日志')
    lines.append('-' * 52)
    lines.extend(doc.last_log or ['(无)'])
    text = '\n'.join(lines) + '\n'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


# --------------------------------------------------------------------------
# CATIA V5 自动化脚本生成
# --------------------------------------------------------------------------


def export_catia_script(doc, path: str) -> str:
    """生成一段可在装有 CATIA V5 + pywin32 的机器上直接运行的自动化脚本。"""
    from . import catia_export
    return catia_export.export_catia_script(doc, path)


def export_catia_json(doc, path: str) -> str:
    """导出给 CATIA 插件(宏)使用的特征表 JSON。"""
    from . import catia_export
    return catia_export.export_catia_json(doc, path)
