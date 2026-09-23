# -*- coding: utf-8 -*-
r"""验证「中文 -> 特征表 -> VBScript 解析」整条链路。

CATIA 不在本机, 但 CATIA 宏里真正容易出错的部分是
"读 Unicode 特征文件 + 按竖线切分 + 还原中文特征名", 这里用 Windows 脚本宿主
(cscript) 把这段逻辑原样跑一遍, 并把结果写成 Unicode 文件回读校验,
避开控制台代码页的干扰。

用法:  python tools\test_catia_bridge.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PARSER_VBS = r'''Dim fso, ts, lines, arr, i, ln, p, q, total, recs(), f, ots
Set fso = CreateObject("Scripting.FileSystemObject")
Set ts = fso.OpenTextFile(WScript.Arguments(0), 1, False, -1)
lines = ts.ReadAll
ts.Close
total = 0
ReDim recs(0)
arr = Split(lines, vbCrLf)
If UBound(arr) < 1 Then arr = Split(lines, vbLf)
For i = 0 To UBound(arr)
    ln = Trim(arr(i))
    If Left(ln, 5) = "FEAT_" And InStr(ln, "=") > 0 Then
        p = InStr(ln, """")
        q = InStrRev(ln, """")
        If q > p Then
            ReDim Preserve recs(total)
            recs(total) = Mid(ln, p + 1, q - p - 1)
            total = total + 1
        End If
    End If
Next
Set ots = fso.CreateTextFile(WScript.Arguments(1), True, True)
ots.Write "COUNT=" & total & vbCrLf
For i = 0 To total - 1
    ots.Write "REC=" & i & "|" & recs(i) & vbCrLf
Next
ots.Close
'''

CASES = [
    ('画一个 100×60×20 的长方体, 中心打一个直径 10 的通孔', 2, '长方体'),
    ('法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120', 8, '圆柱'),
    ('齿轮 模数 3 齿数 24 厚 20, 中心孔直径 20', 2, '齿轮'),
    ('底板 200×150×12, 四角各打一个直径 8 的通孔, 内缩 12', 5, '长方体'),
    ('套筒 外径 80 内径 50 长 100', 2, '圆柱'),
]


def main():
    tmp = tempfile.gettempdir()
    vbs = os.path.join(tmp, 'aim_parser.vbs')
    with open(vbs, 'w', encoding='utf-16') as f:
        f.write(PARSER_VBS)

    passed = failed = 0
    print('== CATIA 桥接链路测试 (中文 -> 特征表 -> cscript 解析 -> Unicode 回读) ==')
    for (text, expect, must_contain) in CASES:
        desc = os.path.join(tmp, 'aim_t_desc.txt')
        feat = os.path.join(tmp, 'aim_t_feat.vbs')
        result = os.path.join(tmp, 'aim_t_result.txt')
        with open(desc, 'w', encoding='utf-16') as f:
            f.write(text)
        for p_ in (feat, result):
            if os.path.exists(p_):
                os.remove(p_)
        p = subprocess.run([sys.executable, '-m', 'ai_modeler', '--catia-vbs', feat,
                            '--text-file', desc], cwd=BASE, capture_output=True,
                           text=True, encoding='utf-8', errors='replace')
        r = subprocess.run(['cscript', '//nologo', vbs, feat, result], capture_output=True,
                           text=True, encoding='utf-8', errors='replace')
        count = -1
        recs = []
        if os.path.isfile(result):
            with open(result, 'r', encoding='utf-16') as f:
                for line in f:
                    line = line.rstrip('\r\n')
                    if line.startswith('COUNT='):
                        count = int(line.split('=')[1])
                    elif line.startswith('REC='):
                        body = line[4:]
                        recs.append(body.split('|', 1)[1] if '|' in body else body)
        ok = (count == expect) and any(must_contain in x for x in recs)
        # 竖线分段数必须恒为 10
        if ok:
            ok = all(len(x.split('|')) == 10 for x in recs)
        passed += 1 if ok else 0
        failed += 0 if ok else 1
        print('  [%s] %-44s 期望 %2d, 实得 %2d, 中文名还原 %s'
              % ('PASS' if ok else 'FAIL', text[:42], expect, count,
                 'OK' if any(must_contain in x for x in recs) else '失败'))
        if ok and recs:
            print('        %s' % recs[0][:96])
        elif not ok:
            print('        python: %s' % ((p.stdout or '') + (p.stderr or '')).strip()[:200])
            print('        cscript: %s' % ((r.stdout or '') + (r.stderr or '')).strip()[:200])
        for p_ in (desc, feat, result):
            try:
                os.remove(p_)
            except OSError:
                pass
    try:
        os.remove(vbs)
    except OSError:
        pass
    print('-' * 62)
    print('通过 %d / %d' % (passed, passed + failed))
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
