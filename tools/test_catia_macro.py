# -*- coding: utf-8 -*-
r"""宏通道端到端真机测试（不需要人点对话框）。

把 **CATIA 里真正会执行的那份 .CATScript** 完整跑一遍:

    部署好的 .CATScript (纯 ASCII)
      -> 加一层 cscript 适配外壳 (补 CATIA 对象 / 把 MsgBox 换成打印)
      -> 宏自己去调 `python -m ai_modeler --catia-ui`（用环境变量喂描述, 跳过对话框）
      -> 宏读回特征表 -> 在 CATIA 里重建 -> 再回读 CATIA 校验特征真的存在

默认先把活动文档切成 **Product (.CATProduct)** —— 这正是「活动文档不是 Part」
导致 `对象不支持此属性或方法: 'doc.Part'` 的场景, 修复后必须能自动新建 Part 并建出模型。

用法:
    python tools\test_catia_macro.py                    用示例法兰, 活动文档设为 Product
    python tools\test_catia_macro.py "画一个 100x60x20 的长方体, 中心打孔"
    python tools\test_catia_macro.py --part-active      活动文档设为 Part
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from ai_modeler import catia  # noqa: E402

MACRO = os.path.join(BASE, 'catia_plugin', 'AI自动建模助手.CATScript')
DEMO = '法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120'

SHIM = """' ---- cscript 适配外壳 (仅测试用, 不改动宏本身逻辑) ----
' 只声明 CATIA: 其余全局变量由宏自己 Dim, 重复声明会报"名称重定义"
Dim CATIA
On Error Resume Next
Set CATIA = GetObject(, "CATIA.Application")
If Err.Number <> 0 Then
    WScript.Echo "ERROR CATIA_NOT_AVAILABLE"
    WScript.Quit 3
End If
Err.Clear
On Error GoTo 0

' 把宏里的 MsgBox 换成打印: 既不弹窗阻塞, 也能看到内容
Sub LogBox(msg, style, title)
    WScript.Echo "MSGBOX[" & title & "] " & msg
End Sub

"""

PREP = r"""' ---- 摆出场景: 把活动文档换成 Product / Part, 并报告当前文档 ----
Dim CATIA, d, i, names
On Error Resume Next
Set CATIA = GetObject(, "CATIA.Application")
If Err.Number <> 0 Then WScript.Quit 3
Err.Clear
Set d = CATIA.Documents.Add("%TYPE%")
If Err.Number <> 0 Then
    WScript.Echo "PREP_FAILED " & Err.Description
    WScript.Quit 4
End If
Err.Clear
WScript.Echo "ACTIVE " & CATIA.ActiveDocument.Name
names = ""
For i = 1 To CATIA.Documents.Count
    names = names & CATIA.Documents.Item(i).Name & " | "
Next
WScript.Echo "DOCS " & names
"""

CHECK = r"""' ---- verify: shapes exist, datum plane heights, active document ----
Dim CATIA, i, d, p, b, j, line, total, hb, hs, k
On Error Resume Next
Set CATIA = GetObject(, "CATIA.Application")
If Err.Number <> 0 Then
    WScript.Echo "CHECK_FAILED"
    WScript.Quit 3
End If
Err.Clear
Set d = CATIA.ActiveDocument
If Err.Number = 0 And Not d Is Nothing Then
    WScript.Echo "ACTIVE " & d.Name
End If
Err.Clear
total = 0
For i = 1 To CATIA.Documents.Count
    Set d = CATIA.Documents.Item(i)
    Err.Clear
    Set p = d.Part
    If Err.Number = 0 And Not p Is Nothing Then
        Set b = p.MainBody
        If Err.Number = 0 And Not b Is Nothing Then
            If b.Shapes.Count > 0 Then
                line = "PART_WITH_SHAPES " & d.Name & " shapes=" & b.Shapes.Count & " :"
                For j = 1 To b.Shapes.Count
                    line = line & " " & b.Shapes.Item(j).Name
                Next
                WScript.Echo line
                total = total + b.Shapes.Count
                For k = 1 To p.HybridBodies.Count
                    Set hb = p.HybridBodies.Item(k)
                    For j = 1 To hb.HybridShapes.Count
                        Set hs = hb.HybridShapes.Item(j)
                        Err.Clear
                        WScript.Echo "PLANE " & d.Name & " " & hs.Name & " z=" & hs.Offset.Value
                        Err.Clear
                    Next
                Next
            End If
        End If
    End If
    Err.Clear
Next
WScript.Echo "TOTAL_SHAPES " & total
"""


def run_vbs(text, timeout=600, env_extra=None):
    import datetime
    stamp = datetime.datetime.now().strftime('%H%M%S_%f')[:-3]
    path = os.path.join(tempfile.gettempdir(), 'aim_macro_e2e_%s.vbs' % stamp)
    with open(path, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write(text)
    env = dict(os.environ)
    env.update(env_extra or {})
    try:
        p = subprocess.run(['cscript', '//nologo', path], capture_output=True,
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout'
    out = p.stdout.decode('gbk', 'replace') if p.stdout else ''
    err = p.stderr.decode('gbk', 'replace') if p.stderr else ''
    try:
        os.remove(path)
    except OSError:
        pass
    return p.returncode, out, err


def main():
    argv = sys.argv[1:]
    part_active = '--part-active' in argv
    text = ' '.join(a for a in argv if not a.startswith('--')) or DEMO

    ok, msg = catia.probe()
    print('CATIA  : %s' % ('已检测到' if ok else '未检测到 -> ' + msg))
    if not ok:
        return 2

    with open(MACRO, 'r', encoding='ascii') as f:
        macro = f.read()
    print('宏文件 : %s (%d 字节, 纯 ASCII)' % (os.path.basename(MACRO), len(macro)))
    print('描述   : %s' % text)
    print('场景   : 活动文档 = %s' % ('Part' if part_active else 'Product (.CATProduct)'))
    print('-' * 64)

    rc, out, err = run_vbs(PREP.replace('%TYPE%', 'Part' if part_active else 'Product'),
                           timeout=180)
    before = set()
    for ln in out.splitlines():
        s = ln.strip()
        if not s:
            continue
        print('  [prep]  ' + s[:150])
        if s.startswith('DOCS '):
            before = {x.strip() for x in s[5:].split('|') if x.strip()}
    if err.strip():
        print('  [prep stderr] ' + err.strip()[:200])
    if rc != 0:
        print('[失败] 准备场景失败')
        return 3

    harness = SHIM + macro.replace('MsgBox ', 'LogBox ') + '\n\nCATMain\n'
    out_file = os.path.join(tempfile.gettempdir(), 'aim_macro_out.txt')
    try:
        os.remove(out_file)
    except OSError:
        pass
    rc, out, err = run_vbs(harness, timeout=180,
                           env_extra={'AI_MODELER_CATIA_TEXT': text})
    for ln in out.splitlines():
        if ln.strip():
            print('  [macro] ' + ln.strip()[:150])
    if err.strip():
        print('  [macro stderr] ' + err.strip()[:300])
    print('  （宏是异步的：上面没输出是正常的，下面等后台写完结果文件）')

    # 3) 等后台结束: 它以写结果文件作为完成标志 (不能只看形状数, 会抢跑)
    deadline = time.time() + 300
    done = False
    while time.time() < deadline:
        if os.path.isfile(out_file) and os.path.getsize(out_file) > 0:
            done = True
            break
        time.sleep(2)
    if done:
        with open(out_file, 'r', encoding='utf-16') as f:
            for ln in f.read().splitlines():
                if ln.strip():
                    print('  [backend] ' + ln.strip()[:150])
    else:
        print('  [backend] 等待超时（300 秒）')

    # 4) 校验 CATIA 里真的建出来了
    rc2, out2, err2 = run_vbs(CHECK, timeout=180)
    shapes = 0
    active = ''
    for ln in out2.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith('ACTIVE '):
            active = s[7:]
            continue
        if s.startswith('PART_WITH_SHAPES'):
            name = s.split()[1]
            if name in before:          # 旧文档, 不计入本次结果
                continue
            print('  [check] ' + s[:150])
            try:
                shapes += int(s.split('shapes=')[1].split()[0])
            except Exception:
                pass
        elif s.startswith('PLANE'):
            print('  [check] ' + s[:150])
    print('  [check] 当前活动文档: %s' % (active or '(未知)'))
    print('-' * 64)

    if shapes > 0 and done:
        print('[通过] 宏端到端跑通: 新建零件里有 %d 个特征' % shapes)
        return 0
    print('[失败] 宏没有在 CATIA 里建出特征')
    return 1


if __name__ == '__main__':
    sys.exit(main())
