# -*- coding: utf-8 -*-
r"""导出 / 视图工具(4 个) —— 对应参考项目 catia-v5-mcp-server 的 export 模块。

    catia_export      文档导出: step/stp, iges/igs, stl, 3dxml, vrml/wrl, pdf
    catia_screenshot  把 3D 视图存成图片
    catia_set_view    标准视图(iso/front/back/left/right/top/bottom)
    catia_fit_all     视图适应全部

中文路径照 document.py 的老规矩: CATIA 的 ExportData/SaveAs 直接写中文路径会卡死,
所以先写到 ASCII 临时文件, 再在 Python 侧 shutil.move 搬过去(真机验证过: STL 中文路径 OK)。

真机结论(本机 CATIA V5-6R2023, 都实测过, 代码就是按这些写的):
    · Document.ExportData(path, "stp"/"igs"/"stl"/"3dxml"/"wrl")  —— 全部可用
      (CATPart 上实测: stl 3244B / stp 8957B / igs 12150B / 3dxml 6058B / wrl 5687B)
    · ExportData(path, "pdf")  —— 零件/装配上失败("方法 ExportData 失败"), 只有 CATDrawing 才有意义
    · Viewer.Reframe                                          —— 可用(catia_fit_all 用它)
    · Viewer3D.Viewpoint3D.PutSightDirection/PutUpDirection   —— 可用(catia_set_view 用它)
      GetSightDirection 能回读, 置 (0,0,-1) 回读就是 (0,0,-1), 说明真的改了视图
    · Viewer.CaptureToFile(iFormat, iFile)                    —— 可用(BMP=4 实测写出 450x600 的 BMP)
      注意: 它**没有** width/height 参数; Viewer.Width/Height 是只读的(赋值报 450)。
      CatCaptureFormat 里没有 PNG, 所以 .png 由本工具在 Python 侧从 BMP 转换(纯 zlib)。
    · 取 3D 视图前必须**先激活窗口**: gApp.ActiveWindow / 窗口的 ActiveViewer 在窗口没到前台
      (或活动文档不是 3D 窗口)时会取不到 —— 工具先做 doc.Activate + Windows(1).Activate,
      并把每一步的 Err.Number(十六进制)与描述记录下来; 全都失败时返回明确提示而不是空报错。
    · Application.StartCommand                                 —— **弃用**:
        StartCommand "Fit All In" 让脚本直接挂死(90s 超时);
        StartCommand <本地化标题>("适应全部")也挂死;
        StartCommand "正视图" 能返回 err=0 但实测视线方向不变(等于没生效)。
    · 所有工具脚本都套了一层保险: Sub aimRun + 调用处的 On Error Resume Next ——
      任何漏网错误都会变成 "工具脚本 aborted: errno=0x…" 的明确文本, 不会静默死掉。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zlib

from ..bridge import CatiaError, run_vbs
from ._schema import S, arg_i, arg_s, obj, vbs

NAME = 'export'


# --------------------------------------------------------------------------
# VBScript 公共片段(纯 ASCII)
# --------------------------------------------------------------------------

_VIEWER = r'''
Dim aimViewWhy

' Render a CATIA error as one explicit line. Even when Err.Description is empty
' the errno (hex) is always included -- otherwise the client gets a blank error.
Function AimErrText(tag)
    Dim n, d
    n = Err.Number
    d = Err.Description
    If Len(d) = 0 Then d = "(CATIA returned an empty description)"
    AimErrText = tag & ": errno=0x" & Hex(n) & " (" & n & ") " & d
End Function

Function AimViewer()
    ' Get the 3D viewer. Order (all verified on the real machine):
    '   1. Attach first -- gApp is Nothing until the COM channel is open, and every
    '      gApp.xxx line would then fail silently.
    '   2. Activate the document and its window (doc.Activate + gApp.Windows(1)
    '      .Activate): when the CATIA window is not in the foreground the viewer
    '      cannot be reached, which looked like "no 3D viewer at all".
    '   3. ActiveWindow.ActiveViewer, then Window.Viewer, then document-level
    '      fallbacks.
    ' NOTE: VBScript's And does not short-circuit, so every object variable is
    ' initialised to Nothing first: otherwise "Err.Number = 0 And Not v Is
    ' Nothing" evaluates "v Is Nothing" on an Empty variable and raises
    ' "object required". Every failure is recorded in aimViewWhy with its errno.
    Dim v, w, d
    Set AimViewer = Nothing
    aimViewWhy = ""
    Attach
    On Error Resume Next
    Err.Clear
    Set d = Nothing
    Set d = gApp.ActiveDocument
    If Err.Number = 0 And Not d Is Nothing Then
        Err.Clear
        d.Activate
        If Err.Number <> 0 Then aimViewWhy = aimViewWhy & AimErrText("Document.Activate") & " ;; "
        Err.Clear
    Else
        aimViewWhy = aimViewWhy & AimErrText("Application.ActiveDocument") & " ;; "
        Err.Clear
    End If

    Set w = Nothing
    Set w = gApp.ActiveWindow
    If Err.Number <> 0 Or w Is Nothing Then
        aimViewWhy = aimViewWhy & AimErrText("Application.ActiveWindow") & " ;; "
        Err.Clear
        Set w = Nothing
        Set w = gApp.Windows.Item(1)
        If Err.Number <> 0 Or w Is Nothing Then
            aimViewWhy = aimViewWhy & AimErrText("Application.Windows.Item(1)") & " ;; "
        Else
            aimViewWhy = aimViewWhy & "(fell back to Application.Windows.Item(1)) ;; "
        End If
        Err.Clear
    End If

    If Not w Is Nothing Then
        Err.Clear
        w.Activate
        If Err.Number <> 0 Then aimViewWhy = aimViewWhy & AimErrText("Window.Activate") & " ;; "
        Err.Clear
        Set v = Nothing
        Set v = w.ActiveViewer
        If Err.Number = 0 And Not v Is Nothing Then
            Set AimViewer = v
        Else
            aimViewWhy = aimViewWhy & AimErrText("Window.ActiveViewer") & " ;; "
            Err.Clear
            Set v = Nothing
            Set v = w.Viewer
            If Err.Number = 0 And Not v Is Nothing Then
                Set AimViewer = v
            Else
                aimViewWhy = aimViewWhy & AimErrText("Window.Viewer") & " ;; "
            End If
            Err.Clear
        End If
    End If

    If AimViewer Is Nothing Then
        Set v = Nothing
        Set v = gApp.ActiveDocument.Viewer(1)
        If Err.Number = 0 And Not v Is Nothing Then Set AimViewer = v
        Err.Clear
    End If
    If AimViewer Is Nothing Then
        Set v = Nothing
        Set v = gApp.ActiveDocument.Windows.Item(1).ActiveViewer
        If Err.Number = 0 And Not v Is Nothing Then Set AimViewer = v
        Err.Clear
    End If
    On Error GoTo 0
End Function

Function AimViewerFail()
    Dim m
    m = "cannot get the 3D viewer (errno=0x" & Hex(Err.Number) & ")"
    If Len(aimViewWhy) > 0 Then m = m & " ;; " & aimViewWhy
    AimViewerFail = m
End Function

Function AimSight(v)
    ' read back the current sight direction (to check whether set_view really worked);
    ' returns an empty array when unavailable
    Dim a(2), x, v3
    Set AimSight = Nothing
    On Error Resume Next
    Err.Clear
    Set v3 = Nothing
    Set v3 = v.Viewpoint3D
    If Err.Number <> 0 Or v3 Is Nothing Then
        Err.Clear
        On Error GoTo 0
        AimSight = Array()
        Exit Function
    End If
    Err.Clear
    v3.GetSightDirection a
    If Err.Number = 0 Then
        Err.Clear
        On Error GoTo 0
        AimSight = a
        Exit Function
    End If
    Err.Clear
    x = v3.GetSightDirection
    If Err.Number = 0 And IsArray(x) Then
        Err.Clear
        On Error GoTo 0
        AimSight = x
        Exit Function
    End If
    Err.Clear
    On Error GoTo 0
    AimSight = Array()
End Function
'''


# --------------------------------------------------------------------------
# Python 侧小工具
# --------------------------------------------------------------------------

def _dry() -> bool:
    return os.environ.get('AI_MODELER_MCP_DRYRUN', '') not in ('', '0', 'false')


def _dry_note(what: str) -> str:
    return ('(干跑模式 AI_MODELER_MCP_DRYRUN=1): 没有真的连 CATIA, 下面是本次会做的事\n  %s'
            % what)


def _cn(text: str) -> str:
    """把非 ASCII 文本转成 VBScript 的 ChrW 表达式 —— 让脚本本身保持纯 ASCII。

    现在模块里没人用它了(StandardNamedViews 那条 StartCommand 路已按真机结论弃用),
    但留着一个已验证可用的小工具: 真机上 `gApp.StartCommand ChrW(&H6B63)...` 是能返回
    err=0 的, 将来要往脚本里塞中文标识符(命令标题等)时用它比直接写中文稳。
    """
    out, buf = [], ''
    for ch in text:
        if ord(ch) < 128:
            buf += ch
        else:
            if buf:
                out.append(vbs(buf))
                buf = ''
            out.append('ChrW(&H%X)' % ord(ch))
    if buf:
        out.append(vbs(buf))
    return ' & '.join(out) if out else '""'


def _ascii(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


# --------------------------------------------------------------------------
# 图片识别(验证截图真的生成了、以及真实格式/尺寸)
# --------------------------------------------------------------------------

_EXT_OF = {'bmp': '.bmp', 'png': '.png', 'jpeg': '.jpg', 'tiff': '.tif',
           'pdf': '.pdf', 'svg': '.svg', 'emf': '.emf', 'cgm': '.cgm'}

# 每种真实格式可接受的扩展名(对不上就顺手改成规范扩展名, 并说明)
_OK_EXT = {'bmp': ('.bmp', '.dib'), 'png': ('.png',), 'jpeg': ('.jpg', '.jpeg'),
           'tiff': ('.tif', '.tiff'), 'pdf': ('.pdf',), 'svg': ('.svg',),
           'emf': ('.emf',), 'cgm': ('.cgm',)}


def _jpeg_size(data: bytes):
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(data[i + 5:i + 7], 'big')
            w = int.from_bytes(data[i + 7:i + 9], 'big')
            return (w, h)
        if m == 0xD8 or 0xD0 <= m <= 0xD9:
            i += 2
            continue
        seg = int.from_bytes(data[i + 2:i + 4], 'big')
        if seg <= 0:
            break
        i += 2 + seg
    return None


def _image_info(path: str):
    """返回 (类型, (宽, 高) 或 None); 认不出来返回 (None, None)。"""
    try:
        with open(path, 'rb') as f:
            head = f.read(64)
    except OSError:
        return (None, None)
    if len(head) < 4:
        return (None, None)
    if head[:2] == b'BM' and len(head) >= 26:
        w = int.from_bytes(head[18:22], 'little', signed=True)
        h = int.from_bytes(head[22:26], 'little', signed=True)
        return ('bmp', (w, abs(h)))
    if head[:8] == b'\x89PNG\r\n\x1a\n' and len(head) >= 24:
        return ('png', (int.from_bytes(head[16:20], 'big'),
                        int.from_bytes(head[20:24], 'big')))
    if head[:3] == b'\xff\xd8\xff':
        try:
            with open(path, 'rb') as f:
                data = f.read(4 * 1024 * 1024)
            return ('jpeg', _jpeg_size(data))
        except OSError:
            return ('jpeg', None)
    if head[:4] == b'%PDF':
        return ('pdf', None)
    if head[:4] in (b'II*\x00', b'MM\x00*'):
        return ('tiff', None)
    if head.lstrip()[:5] in (b'<?xml', b'<svg '):
        return ('svg', None)
    if head[:4] == b'\x01\x00\x00\x00':
        return ('emf', None)
    return (None, None)


# --------------------------------------------------------------------------
# catia_export
# --------------------------------------------------------------------------

# 用户给的 format -> (ExportData 的格式串, 规范扩展名)
_FORMATS = {
    'step': ('stp', '.stp'),
    'stp': ('stp', '.stp'),
    'iges': ('igs', '.igs'),
    'igs': ('igs', '.igs'),
    'stl': ('stl', '.stl'),
    '3dxml': ('3dxml', '.3dxml'),
    'vrml': ('wrl', '.wrl'),
    'wrl': ('wrl', '.wrl'),
    'pdf': ('pdf', '.pdf'),
}


def t_export(args):
    fmt = (arg_s(args, 'format') or '').strip().lower()
    path = arg_s(args, 'path').strip()
    if fmt not in _FORMATS:
        raise ValueError('format 只支持 %s, 收到: %r'
                         % ('/'.join(sorted(_FORMATS)), fmt))
    if not path:
        raise ValueError('缺少参数 path(导出文件的完整路径)')
    overwrite = False
    if args.get('overwrite') is not None:
        overwrite = str(args.get('overwrite')).strip().lower() in ('1', 'true', 'yes', 'y', 'on', '是')
    catia_fmt, ext = _FORMATS[fmt]
    path = os.path.abspath(path)
    if not os.path.splitext(path)[1]:
        path += ext
    if _dry():
        return _dry_note('把活动文档用 ExportData 导出成 %s -> %s'
                         % (catia_fmt.upper(), path))
    if os.path.exists(path) and not overwrite:
        raise ValueError('文件已存在: %s (要覆盖请传 overwrite=true)' % path)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)

    # 中文路径绕行: 先写成 ASCII 临时文件, 再搬
    moved_from = None
    if _ascii(path):
        target = path
    else:
        target = os.path.join(tempfile.gettempdir(),
                             'aim_mcp_export_%d%s' % (os.getpid() % 100000, ext))
        moved_from = target
        if os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass

    body = _VIEWER + '''
Dim aimD, aimExpErr
Sub aimRun()
    Set aimD = Act()
    If aimD Is Nothing Then Fail "no active document (nothing to export)"
    Out "doc", aimD.Name
    Out "type", TypeName(aimD)
    On Error Resume Next
    Err.Clear
    aimD.ExportData %s, "%s"
    If Err.Number <> 0 Then
        aimExpErr = AimErrText("Document.ExportData(" & "%s" & ")")
        Err.Clear
        On Error GoTo 0
        Fail aimExpErr
    End If
    On Error GoTo 0
    Out "path", %s
End Sub

' Safety net: no unhandled error may kill the script silently
On Error Resume Next
aimRun
If Err.Number <> 0 Then Fail AimErrText("export script aborted")
On Error GoTo 0
''' % (vbs(target), catia_fmt, catia_fmt, vbs(target))

    try:
        r = run_vbs(body, timeout=600)
    except CatiaError as e:
        err = str(e)
        out = ['导出失败(format=%s -> %s):' % (fmt, path),
               '  CATIA 侧原始报错: %s' % err]
        if catia_fmt == 'pdf':
            out.append('  说明: CATIA 的 ExportData("pdf") 只对工程图(CATDrawing)有效; '
                       '3D 零件(CATPart)/装配里没有可靠的 PDF 导出通道。')
        elif catia_fmt == 'stl':
            out.append('  说明: STL 导出会用到 CATIA 的 Options → Compatibility → STL 设置; '
                       '若弹了对话框, 脚本会卡住/超时(桥只等 600s)。')
        out.append('  提示: 先确认活动文档存在(catia_get_active_document_info), '
                   '并确认没有 CATIA 模态弹窗挡在前面。')
        return '\n'.join(out)

    v = r.get('vars') or {}
    if not os.path.isfile(target):
        return ('导出看起来执行了, 但没有生成文件: %s\n  (CATIA 可能只弹了导出对话框; '
                '文档类型 %s 可能不支持 %s)' % (target, v.get('type', '?'), catia_fmt))
    size = os.path.getsize(target)
    if moved_from:
        shutil.move(target, path)
    out = ['已导出: %s' % path,
           '  文档: %s (%s)' % (v.get('doc', '?'), v.get('type', '?')),
           '  格式: %s (CATIA ExportData 的格式串 "%s")' % (fmt, catia_fmt),
           '  文件大小: %d 字节' % size]
    if moved_from:
        out.append('  (中文路径绕行: 先导出到 ASCII 临时文件再搬移 —— '
                   'CATIA 的 ExportData 直接写中文路径会卡死)')
    if catia_fmt == 'stl':
        out.append('  说明: STL 是 ASCII 还是二进制由 CATIA 的 Options → Compatibility → STL 决定; '
                   '本项目 tools\\check_catia_solid.py 读的是 ASCII 网格(这次导出的文件%s)'
                   % ('看起来是 ASCII 网格' if _ascii_head(path) else '看起来是二进制, 需要先转成 ASCII'))
    if catia_fmt == 'pdf':
        out.append('  说明: PDF 只在工程图(CATDrawing)上有意义, 零件导出会被 CATIA 拒绝。')
    return '\n'.join(out)


def _ascii_head(path: str, n: int = 512) -> bool:
    """粗略判断 STL 是不是 ASCII 格式。"""
    try:
        with open(path, 'rb') as f:
            head = f.read(n)
    except OSError:
        return True
    try:
        head.decode('ascii')
        return b'solid' in head.lower() or b'facet' in head.lower()
    except UnicodeDecodeError:
        return False


# --------------------------------------------------------------------------
# catia_screenshot
# --------------------------------------------------------------------------

# CatCaptureFormat 枚举(按 InfTypeLib 里的声明顺序):
#   CGM=0, EMF=1, TIFF=2, TIFFGreyScale=3, BMP=4, JPEG=5, PDF=6, SVG=7
# 注意: 这个版本**没有 PNG**。
_CAPTURE_FMT = {
    '.bmp': 4, '.dib': 4,
    '.jpg': 5, '.jpeg': 5,
    '.tif': 2, '.tiff': 2,
    '.pdf': 6,
    '.svg': 7,
    '.emf': 1,
    '.cgm': 0,
}
_DEFAULT_CAPTURE = 4  # BMP: 无损、肯定有


def _bmp_to_png(bmp_path: str, png_path: str):
    """把 CATIA 抓出来的 BMP 转成 PNG(纯标准库 zlib)。

    CATIA 的 CatCaptureFormat 里**没有 PNG**(只有 CGM/EMF/TIFF/TIFFGreyScale/BMP/
    JPEG/PDF/SVG), 而客户端基本都想要 PNG, 所以先在 CATIA 侧抓 BMP, 再在这里转 ——
    转出来的像素与 CATIA 抓的完全一致, 不做任何缩放。
    返回 (宽, 高)。
    """
    with open(bmp_path, 'rb') as f:
        d = f.read()
    if len(d) < 54 or d[:2] != b'BM':
        raise ValueError('not a BMP file (magic=%r)' % d[:2])
    off = int.from_bytes(d[10:14], 'little')
    w = int.from_bytes(d[18:22], 'little', signed=True)
    h = int.from_bytes(d[22:26], 'little', signed=True)
    bpp = int.from_bytes(d[28:30], 'little')
    comp = int.from_bytes(d[30:34], 'little')
    if comp != 0:
        raise ValueError('compressed BMP (biCompression=%d) not supported' % comp)
    if bpp not in (24, 32):
        raise ValueError('BMP bit depth %d not supported (need 24/32)' % bpp)
    if w <= 0 or h == 0:
        raise ValueError('bad BMP size %dx%d' % (w, h))
    top_down = h < 0
    h = abs(h)
    n = bpp // 8
    stride = ((w * n + 3) // 4) * 4
    rows = []
    for y in range(h):
        sy = y if top_down else (h - 1 - y)          # BMP 默认是自下而上
        start = off + sy * stride
        line = d[start:start + w * n]
        if len(line) < w * n:
            raise ValueError('truncated BMP (row %d)' % y)
        px = bytearray(w * n)
        if n == 3:
            px[0::3] = line[2::3]                    # BGR -> RGB
            px[1::3] = line[1::3]
            px[2::3] = line[0::3]
        else:
            px[0::4] = line[2::4]
            px[1::4] = line[1::4]
            px[2::4] = line[0::4]
            px[3::4] = line[3::4]
        rows.append(b'\x00' + bytes(px))             # 每行前缀 filter=0(None)
    raw = b''.join(rows)
    ctype = 2 if n == 3 else 6

    def chunk(tag, data):
        return (len(data).to_bytes(4, 'big') + tag + data +
                zlib.crc32(tag + data).to_bytes(4, 'big'))

    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', w.to_bytes(4, 'big') + h.to_bytes(4, 'big')
                   + bytes([8, ctype, 0, 0, 0]))
           + chunk(b'IDAT', zlib.compress(raw, 6))
           + chunk(b'IEND', b''))
    with open(png_path, 'wb') as f:
        f.write(png)
    return (w, h)


def t_screenshot(args):
    path = arg_s(args, 'path').strip()
    if not path:
        raise ValueError('缺少参数 path(图片保存路径, 例如 "D:\\\\out\\\\view.png"; '
                         'CATIA 侧只抓得到 BMP/JPEG/TIFF/PDF/SVG/EMF/CGM, PNG 由本工具在 '
                         'Python 侧从 BMP 转出来)')
    w = arg_i(args, 'width', 0)
    h = arg_i(args, 'height', 0)
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    if not ext:
        ext = '.png'
        path += ext
    want_png = ext == '.png'
    # CATIA 没有 PNG: 想要 png 就先在 CATIA 侧抓 BMP(4), 之后在 Python 侧转
    if want_png or ext not in _CAPTURE_FMT:
        cap_fmt, cap_ext = _DEFAULT_CAPTURE, '.bmp'
    else:
        cap_fmt, cap_ext = _CAPTURE_FMT[ext], ext
    if _dry():
        return _dry_note('在 CATIA 里 v.Reframe + v.CaptureToFile(%d, <ASCII 临时文件%s>)%s, '
                         '成功后写到 %s'
                         % (cap_fmt, cap_ext,
                            '(再在 Python 侧转成 PNG)' if want_png else '', path))
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)

    tmp = os.path.join(tempfile.gettempdir(),
                       'aim_mcp_shot_%d%s' % (os.getpid() % 100000, cap_ext))
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass

    body = _VIEWER + '''
Dim aimV, aimTries, aimOK, aimHow, aimCapErr
Sub aimRun()
    Set aimV = AimViewer()
    If aimV Is Nothing Then Fail AimViewerFail()
    Out "viewer", TypeName(aimV)
    On Error Resume Next
    Err.Clear
    Out "viewer_size", CStr(aimV.Width) & "x" & CStr(aimV.Height)
    Err.Clear
    aimV.Reframe
    If Err.Number <> 0 Then Out "reframe_err", AimErrText("Viewer.Reframe")
    Err.Clear
    On Error GoTo 0

    aimOK = 0
    aimHow = ""
    aimTries = ""
    Call AimCap(1)
    If gFso.FileExists(%s) Then
        aimOK = 1
        aimHow = "CaptureToFile(iFormat,iFile)"
    End If
    aimTries = aimTries & "CaptureToFile(iFormat,iFile): " & aimCapErr & " | "
    If aimOK = 0 Then
        Call AimCap(2)
        If gFso.FileExists(%s) Then
            aimOK = 2
            aimHow = "CaptureToFile(iFormat,iFile,Nothing)"
        End If
        aimTries = aimTries & "CaptureToFile(iFormat,iFile,Nothing): " & aimCapErr & " | "
    End If
    On Error Resume Next
    Err.Clear
    Out "viewer_size_after", CStr(aimV.Width) & "x" & CStr(aimV.Height)
    Err.Clear
    On Error GoTo 0
    If aimOK > 0 Then
        Out "ok", "1"
        Out "how", aimHow
        Out "image", %s
        Out "image_size", CStr(gFso.GetFile(%s).Size)
    Else
        Out "ok", "0"
        Out "tries", aimTries
    End If
End Sub

Sub AimCap(mode)
    ' Real machine (V5-6R2023): CaptureToFile(format, file) with 2 args works (BMP = 4
    ' wrote a real 450x600 24-bit BMP); viewer.Width/Height are read-only (assigning
    ' raises 450), so a requested resolution cannot be applied.
    On Error Resume Next
    Err.Clear
    If mode = 1 Then
        aimV.CaptureToFile %d, %s
    ElseIf mode = 2 Then
        aimV.CaptureToFile %d, %s, Nothing
    End If
    If Err.Number <> 0 Then
        aimCapErr = AimErrText("CaptureToFile")
    Else
        aimCapErr = ""
    End If
    Err.Clear
    On Error GoTo 0
End Sub

' Safety net: no unhandled error may kill the script silently
On Error Resume Next
aimRun
If Err.Number <> 0 Then Fail AimErrText("screenshot script aborted")
On Error GoTo 0
''' % (vbs(tmp), vbs(tmp), vbs(tmp), vbs(tmp), cap_fmt, vbs(tmp), cap_fmt, vbs(tmp))
    v = {}
    try:
        r = run_vbs(body, timeout=300)
        v = r.get('vars') or {}
    except CatiaError as e:
        return '\n'.join([
            '截图失败 —— CATIA 侧原始报错:',
            '  %s' % e,
            _viewer_hint(str(e)),
            _shot_alt()])

    if v.get('ok') != '1' or not os.path.isfile(tmp):
        return '\n'.join([
            '截图失败: CATIA 的 Viewer.CaptureToFile 这次没写出图片。',
            '  视图对象: %s   视图尺寸: %s' % (v.get('viewer', '?'), v.get('viewer_size', '?')),
            '  各次尝试的原始报错:',
            '    %s' % (v.get('tries') or '(没有回传)'),
            _shot_alt()])

    kind, size_px = _image_info(tmp)
    final = path
    note = []
    if want_png and kind == 'bmp':
        # CATIA 抓的是 BMP, 在 Python 侧转成 PNG(纯 zlib), 像素完全一致
        try:
            size_px = _bmp_to_png(tmp, final)
            kind = 'png'
            note.append('  CATIA 侧只支持 BMP/JPEG/TIFF/PDF/SVG/EMF/CGM(没有 PNG), '
                        '所以先在 CATIA 抓 BMP, 再在本工具里用 zlib 转成 PNG(不缩放、不重采样)')
        except Exception as e:                                      # noqa: BLE001
            kind = 'bmp'
            final = os.path.splitext(path)[0] + '.bmp'
            note.append('  PNG 转换失败(%s), 已保留 CATIA 抓的 BMP' % e)
        try:
            os.remove(tmp)
        except OSError:
            pass
    else:
        if kind and os.path.splitext(final)[1].lower() not in _OK_EXT.get(kind, ()):
            final = os.path.splitext(final)[0] + _EXT_OF[kind]
        if os.path.exists(final):
            try:
                os.remove(final)
            except OSError:
                pass
        shutil.move(tmp, final)

    out = ['已截图: %s' % final,
           '  视图对象: %s' % v.get('viewer', '?'),
           '  成功方式: %s' % v.get('how', '?'),
           '  文件大小: %d 字节' % os.path.getsize(final)]
    if kind:
        out.append('  真实格式: %s%s' % (kind.upper(),
                                    ('  %dx%d 像素' % size_px) if size_px else ''))
    else:
        out.append('  真实格式: 认不出来(文件头不是 bmp/png/jpg/tiff/pdf/svg/emf) —— '
                   '文件已生成, 但请自己确认内容')
    out.extend(note)
    if final != path:
        out.append('  注意: 你要的扩展名 %s 这次没写成, 实际文件是 %s'
                   % (os.path.splitext(path)[1], final))
    if v.get('viewer_size_after'):
        out.append('  视图尺寸(抓之前/之后): %s / %s'
                   % (v.get('viewer_size', '?'), v.get('viewer_size_after')))
    if w > 0 and h > 0:
        out.append('  关于 width/height: 这两个参数 CATIA 这边用不了 —— '
                   'CaptureToFile(iFormat, iFile, Camera) 不接受尺寸, 而 viewer.Width/Height '
                   '是只读的(真机赋值报 450 错误的参数个数或无效的参数属性值)。'
                   '实际分辨率就是视图窗口的像素尺寸, 上面已按图片头读出真实值。')
    if v.get('reframe_err'):
        out.append('  另外 Reframe 报错: %s' % v['reframe_err'])
    return '\n'.join(out)


def _viewer_hint(err: str) -> str:
    """取不到 3D 视图时, 给一句能直接照做的话。"""
    if 'viewer' in err.lower() or 'window' in err.lower():
        return ('  → CATIA 窗口未激活/最小化(或活动文档没有 3D 视图): 请把 CATIA 窗口切到前台、'
                '并让要操作的零件处于活动窗口, 再重试。工具已经先试过 doc.Activate 与 '
                'Windows.Item(1).Activate 了。')
    return ''


def _shot_alt() -> str:
    return ('排查/替代:\n'
            '  1. CaptureToFile 本身在本机是**可用**的(实测 BMP/JPEG 都能写出 450x600 的图), '
            '所以走到这里通常是"取不到 3D 视图"(窗口被最小化/活动文档不是 3D 视图)或有模态弹窗挡着 —— '
            '先把 CATIA 窗口切到 3D 视图再试。\n'
            '  2. 前台截屏(Windows 侧): 把 CATIA 窗口置前后用 PrintWindow/BitBlt 或 .NET '
            'Graphics.CopyFromScreen 抓 —— 需要前台窗口, 最小化/被挡住会抓成空白; 这要额外依赖, '
            '不在本模块的纯标准库范围内。\n'
            '  3. catia_export(format="pdf") 只在工程图(CATDrawing)上有意义, 3D 零件没有'
            '可靠的 PDF 导出通道。\n'
            '  4. 只想看几何: catia_export(format="stl") 导出网格, 或 catia_get_inertia。')


# --------------------------------------------------------------------------
# catia_set_view / catia_fit_all
# --------------------------------------------------------------------------

# 标准视图的视线方向(sight = 相机看向的方向, up = 画面上方)。
# 依据: 真机上 GetSightDirection 回读 "正视" 是 (0,1,0), 且 PutSightDirection 真的能改视图
# (置 (0,0,-1) 后回读就是 (0,0,-1))。
# 命令方式(Application.StartCommand "Isometric View"/"正视图")已按真机结论**弃用**:
#   · StartCommand "Fit All In" 让脚本挂死(90s 超时);
#   · StartCommand <本地化标题 "正视图"> 返回 err=0 但视线方向不变(等于没生效);
#   · 另一种本地化标题(适应全部)同样挂死。
# 所以这里只用 Viewpoint3D 这条已真机验证的路。
_VIEWS = {
    'iso': ('等轴测', ['等轴测视图'], (-1.0, 1.0, -1.0), (0.0, 0.0, 1.0)),
    'front': ('正视', ['正视图'], (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    'back': ('背视', ['背视图'], (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    'left': ('左视', ['左视图'], (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    'right': ('右视', ['右视图'], (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    'top': ('俯视', ['俯视图'], (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    'bottom': ('仰视', ['仰视图'], (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
}


def t_set_view(args):
    view = (arg_s(args, 'view') or '').strip().lower()
    if view not in _VIEWS:
        raise ValueError('view 只支持 %s, 收到: %r' % ('/'.join(_VIEWS), view))
    cn, titles, sight, up = _VIEWS[view]
    if _dry():
        return _dry_note('把 3D 视图切成%s: Viewpoint3D.PutSightDirection(%r,%r,%r) + '
                         'PutUpDirection, 然后 GetSightDirection 回读核对'
                         % (cn, sight[0], sight[1], sight[2]))
    body = _VIEWER + '''
Dim aimV, aimV3, aimOK, aimHow, aimTries, aimSd(2), aimUd(2), aimSightArr, aimAfter
Sub aimRun()
' NOTE: VBScript identifiers are case-insensitive, so a local variable must not
' share a name with a function: "aimSight" would clash with Function AimSight
' (-> "name redefined"). Hence aimSightArr.
Set aimV = AimViewer()
If aimV Is Nothing Then Fail AimViewerFail()
Out "viewer", TypeName(aimV)
aimOK = 0
aimHow = ""
aimTries = ""
On Error Resume Next
Err.Clear
Set aimV3 = Nothing
Set aimV3 = aimV.Viewpoint3D
If Err.Number <> 0 Or aimV3 Is Nothing Then
    aimTries = aimTries & AimErrText("Viewer.Viewpoint3D") & " | "
Else
    aimSd(0) = %r
    aimSd(1) = %r
    aimSd(2) = %r
    aimUd(0) = %r
    aimUd(1) = %r
    aimUd(2) = %r
    Err.Clear
    aimV3.PutSightDirection aimSd
    If Err.Number = 0 Then
        aimV3.PutUpDirection aimUd
        If Err.Number = 0 Then
            aimOK = 1
            aimHow = "Viewpoint3D.PutSightDirection/PutUpDirection(array)"
        Else
            aimTries = aimTries & AimErrText("PutUpDirection(array)") & " | "
        End If
    Else
        aimTries = aimTries & AimErrText("PutSightDirection(array)") & " | "
    End If
    If aimOK = 0 Then
        Err.Clear
        aimV3.PutSightDirection Array(%r, %r, %r)
        If Err.Number = 0 Then
            aimV3.PutUpDirection Array(%r, %r, %r)
            If Err.Number = 0 Then
                aimOK = 2
                aimHow = "Viewpoint3D.Put*Direction(Array(...))"
            End If
        End If
        If aimOK = 0 Then
            aimTries = aimTries & AimErrText("Put*Direction(Array)") & " | "
        End If
    End If
End If
Err.Clear
On Error GoTo 0
aimAfter = ""
aimSightArr = AimSight(aimV)
If IsArray(aimSightArr) Then
    If UBound(aimSightArr) >= 2 Then
        aimAfter = CStr(aimSightArr(0)) & "," & CStr(aimSightArr(1)) & "," & CStr(aimSightArr(2))
    End If
End If
Out "ok", CStr(aimOK)
Out "how", aimHow
Out "tries", aimTries
Out "expected_sight", "%r,%r,%r"
Out "sight_after", aimAfter
End Sub

' Safety net: no unhandled error may kill the script silently
On Error Resume Next
aimRun
If Err.Number <> 0 Then Fail AimErrText("set_view script aborted")
On Error GoTo 0
''' % (sight[0], sight[1], sight[2], up[0], up[1], up[2],
       sight[0], sight[1], sight[2], up[0], up[1], up[2],
       sight[0], sight[1], sight[2])
    v = {}
    try:
        r = run_vbs(body, timeout=300)
        v = r.get('vars') or {}
    except CatiaError as e:
        return '\n'.join([
            '切换到%s视图: 没成功 —— CATIA 侧原始报错:' % cn,
            '  %s' % e,
            _viewer_hint(str(e)),
            '  (走的是 Viewer3D.Viewpoint3D.PutSightDirection/PutUpDirection —— 真机验证可用的那条路)'])
    ok = v.get('ok') not in (None, '', '0')
    out = ['切换到%s视图: %s' % (cn, '已执行' if ok else '没成功'),
           '  方式: %s' % (v.get('how') or '(没有一条生效)')]
    if v.get('viewer'):
        out.append('  视图对象: %s' % v['viewer'])
    out.append('  工具给的视线方向(期望): %s' % (v.get('expected_sight') or '?'))
    after = v.get('sight_after')
    if after:
        out.append('  CATIA 回读的视线方向: %s' % after)
        exp = [float(x) for x in str(v.get('expected_sight')).split(',')]
        got = [float(x.replace(',', '.')) for x in str(after).split(',')]
        dot = sum(a * b for a, b in zip(exp, got))
        if dot < 0:
            out.append('  ⚠ 回读方向与期望相反: 本机 CATIA 的视线方向约定可能与工具的假设相反, '
                       '但视图确实被改过了(请按实际画面判断)。')
        elif dot < 0.7 * (sum(a * a for a in exp) ** 0.5) * (sum(b * b for b in got) ** 0.5):
            out.append('  ⚠ 回读方向与期望不太一致(可能命令只做了近似设置)。')
        else:
            out.append('  回读核对: 方向与期望一致。')
    else:
        out.append('  CATIA 回读视线方向: 取不到(Viewpoint3D.GetSightDirection 也不行) —— '
                   '所以只能靠肉眼确认画面方向。')
    if not ok:
        out.append('  各次尝试的原始报错:')
        out.append('    %s' % (v.get('tries') or '(没有回传)'))
        out.append('  说明: 走的是 Viewer3D.Viewpoint3D.PutSightDirection/PutUpDirection'
                   '(真机验证可用的那条路, 已试 ByRef 数组与 Array() 两种传法)。'
                   'Application.StartCommand 那条路**故意没用** —— 真机上 StartCommand "Fit All In" '
                   '和本地化标题都会让脚本挂死, 而能返回的本地化标题又实测不改视图。')
    return '\n'.join(out)


def t_fit_all(args):
    if _dry():
        return _dry_note('先激活窗口, 再调 Viewer.Reframe(真机验证可用; 不再用 StartCommand —— 它会挂死)')
    body = _VIEWER + '''
Dim aimV, aimOK, aimHow, aimTries
Sub aimRun()
Set aimV = AimViewer()
If aimV Is Nothing Then Fail AimViewerFail()
Out "viewer", TypeName(aimV)
aimOK = 0
aimHow = ""
aimTries = ""
On Error Resume Next
Err.Clear
aimV.Reframe
If Err.Number = 0 Then
    aimOK = 1
    aimHow = "Viewer.Reframe"
Else
    aimTries = aimTries & AimErrText("Viewer.Reframe") & " | "
End If
Err.Clear
On Error GoTo 0
If aimOK = 0 Then
    On Error Resume Next
    Err.Clear
    aimV.FitAllIn
    If Err.Number = 0 Then
        aimOK = 2
        aimHow = "Viewer.FitAllIn"
    Else
        aimTries = aimTries & AimErrText("Viewer.FitAllIn") & " | "
    End If
    Err.Clear
    On Error GoTo 0
End If
Out "ok", CStr(aimOK)
Out "how", aimHow
Out "tries", aimTries
Out "viewer_size", CStr(aimV.Width) & "x" & CStr(aimV.Height)
End Sub

' Safety net: no unhandled error may kill the script silently
On Error Resume Next
aimRun
If Err.Number <> 0 Then Fail AimErrText("fit_all script aborted")
On Error GoTo 0
'''
    v = {}
    try:
        r = run_vbs(body, timeout=300)
        v = r.get('vars') or {}
    except CatiaError as e:
        return '\n'.join([
            '视图适应全部(Fit All): 没成功 —— CATIA 侧原始报错:',
            '  %s' % e,
            _viewer_hint(str(e)),
            '  (没有再退到 Application.StartCommand: 真机上它会把脚本挂死, 见 catia_set_view 的说明。)'])
    if v.get('ok') not in (None, '', '0'):
        return '\n'.join([
            '视图已适应全部(Fit All): 成功',
            '  方式: %s' % v.get('how', '?'),
            '  视图对象: %s   视图尺寸: %s' % (v.get('viewer', '?'), v.get('viewer_size', '?'))])
    return '\n'.join([
        '视图适应全部: 没成功',
        '  各次尝试的原始报错:',
        '    %s' % (v.get('tries') or '(没有回传)'),
        '  → CATIA 窗口未激活/最小化(或活动文档没有 3D 视图): 请把 CATIA 窗口切到前台再重试。'
        '工具已经先试过 doc.Activate 与 Windows.Item(1).Activate 了。',
        '  (没有再退到 Application.StartCommand: 真机上它会把脚本挂死, 见 catia_set_view 的说明。)'])


# --------------------------------------------------------------------------
# 工具表
# --------------------------------------------------------------------------

TOOLS = [
    ('catia_export',
     '把活动文档导出成 step(stp)/iges(igs)/stl/3dxml/vrml(wrl)/pdf。中文路径自动绕行'
     '(先写 ASCII 临时文件再搬移)。',
     obj({'format': S('导出格式: step|stp|iges|igs|stl|3dxml|vrml|wrl|pdf'),
          'path': S('导出文件的完整路径(建议带上扩展名)'),
          'overwrite': S('目标文件已存在时是否覆盖(默认 false, 会直接报错)')},
         ['format', 'path']),
     t_export),

    ('catia_screenshot',
     '把 CATIA 的 3D 视图存成图片。CATIA 侧只支持 bmp/jpg/tif/pdf/svg/emf/cgm(**没有 PNG**), '
     '所以 .png 会先在 CATIA 抓 BMP, 再在本工具里用 zlib 转成 PNG(不缩放)。'
     '取不到图片会如实报出原始报错(含 errno 十六进制)。',
     obj({'path': S('图片保存路径(如 D:\\\\out\\\\view.png 或 .bmp/.jpg)'),
          'width': S('期望宽度(像素, 可选)。注意: CATIA 的 CaptureToFile 不接受尺寸、'
                     'viewer.Width 也只读, 所以这个参数实际改不了分辨率, 只作记录'),
          'height': S('期望高度(像素, 可选, 同上)')},
         ['path']),
     t_screenshot),

    ('catia_set_view',
     '把 3D 视图切成标准视图: iso/front/back/left/right/top/bottom。'
     '先激活文档与窗口(doc.Activate + Windows(1).Activate), 再走 '
     'Viewer3D.Viewpoint3D 的 PutSightDirection/PutUpDirection(真机验证可用), '
     '最后用 GetSightDirection 回读核对; 取不到窗口/视图时给明确提示与原始报错。',
     obj({'view': S('iso|front|back|left|right|top|bottom')}, ['view']),
     t_set_view),

    ('catia_fit_all',
     '视图适应全部(Fit All): 先激活窗口, 再走 Viewer.Reframe(真机验证可用), '
     '失败再试 Viewer.FitAllIn。',
     obj({}), t_fit_all),
]
