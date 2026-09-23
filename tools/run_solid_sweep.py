# -*- coding: utf-8 -*-
"""在真机上跑一批代表性零件的几何校验(check_catia_solid)。"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
CASES = [
    ('三角转子(孔不在原点)', '三角转子 外接圆半径60 厚度40 中心孔24'),
    ('齿轮中心孔', '齿轮 模数3 齿数24 厚20, 中心孔直径20'),
    ('底板四角孔', '长方体 200×150×12 四角各打一个直径8的通孔 内缩12'),
    ('长方体顶面凹槽', '长方体 100×60×20 顶面凹槽 30×20 深5'),
    ('带轮毂齿轮+中心孔', '直齿圆柱齿轮 模数2 齿数30 厚20, 轮毂外径36, 中心孔直径15'),
    ('法兰内孔', '法兰 外径160 厚20 内径60'),
]
env = dict(os.environ, PYTHONIOENCODING='utf-8')
fails = []
for label, text in CASES:
    print('=' * 70)
    print('>>> %s : %s' % (label, text))
    p = subprocess.run([PY, os.path.join(BASE, 'tools', 'check_catia_solid.py'), text],
                       capture_output=True, env=env, cwd=BASE, timeout=1200)
    out = p.stdout.decode('utf-8', 'replace')
    print(out)
    if p.returncode != 0:
        fails.append((label, p.returncode, out.strip().splitlines()[-1:]))
print('=' * 70)
if fails:
    print('失败 %d 例:' % len(fails))
    for f in fails:
        print('  - %s  (exit %s) %s' % (f[0], f[1], f[2]))
    sys.exit(1)
print('全部通过')
