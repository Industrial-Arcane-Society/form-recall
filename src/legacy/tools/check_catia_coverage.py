# -*- coding: utf-8 -*-
r"""覆盖自检: 零件库里的每一类零件能不能导出到 CATIA。

做法: 用零件库自带的样本参数（partlib.TYPICAL）拼出一句中文描述,
      走真实链路「描述 -> nlparser/partlib -> 特征树 -> build_catia_features」,
      统计导出成多少个 CATIA 操作。

用法:
    python tools\check_catia_coverage.py              汇总
    python tools\check_catia_coverage.py -v           逐个列出
    python tools\check_catia_coverage.py 涡旋盘        只看某一个
"""
from __future__ import annotations

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from ai_modeler import catia_export as CE  # noqa: E402
from ai_modeler import docmodel as DM  # noqa: E402
from ai_modeler import kernel as K  # noqa: E402
from ai_modeler import nlparser, partlib  # noqa: E402


def zrange_note(doc, feats):
    """比对「内核几何的 z 范围」与「导出到 CATIA 的凸台 z 范围」。

    两者不一致是最阴险的一类 bug: 每步都报"成功", 但模型位置不对,
    于是按包围盒算出来的通孔切不穿 —— 表现就是"建出来没有中心孔"。
    这里把它主动查出来。
    """
    try:
        bb = K.bbox(doc.build())
    except Exception:
        return None
    pads = [e for e in feats if str(e['kind']).endswith('_pad')]
    if not bb or not pads:
        return None
    lo, hi = bb[2], bb[5]
    pad_lo = min(e['z'] for e in pads)
    pad_hi = max(e['z'] + e['h'] for e in pads)
    if abs(pad_lo - lo) > 1.0 or abs(pad_hi - hi) > 1.0:
        return '几何 z 范围不一致: 内核 %.1f~%.1f, 导出 %.1f~%.1f' % (lo, hi, pad_lo, pad_hi)
    return None


def all_specs():
    out = {}
    for tbl in (partlib.PARTS, getattr(partlib, 'MEDIUM_PARTS', {}),
                getattr(partlib, 'HARD_PARTS', {})):
        out.update(tbl)
    return out


def phrase_for(name, spec, sample):
    """按零件库登记的参数别名, 把样本参数拼成一句人话。"""
    params = spec.get('params') or {}
    bits = []
    for key, value in (sample or {}).items():
        aliases = params.get(key) or []
        if not aliases:
            continue
        if isinstance(value, (list, tuple)):
            value = value[0]
        if isinstance(value, float) and abs(value - round(value)) < 1e-9:
            value = int(round(value))
        bits.append('%s%s' % (aliases[0], value))
    return (name + ' ' + ' '.join(bits)).strip()


def main():
    argv = sys.argv[1:]
    verbose = '-v' in argv
    only = [a for a in argv if not a.startswith('-')]

    specs = all_specs()
    samples = getattr(partlib, 'TYPICAL', {})
    rows = []

    for name, spec in specs.items():
        if only and not any(o in name for o in only):
            continue
        sample = samples.get(name)
        if not sample:
            rows.append((name, '-', 0, ['没有样本参数']))
            continue
        text = phrase_for(name, spec, sample)
        feats = []
        try:
            r = partlib.build_features(text)          # -> (类别, 特征列表, 期望外形)
            if r:
                feats = list(r[1] or [])
        except Exception as e:
            rows.append((name, text, 0, ['解析异常: %s' % e]))
            continue
        if not feats:
            res = nlparser.parse(text)                # 退回通用解析器
            feats = list(res.doc.features) if res.ok else []
        if not feats:
            rows.append((name, text, 0, ['没解析出特征']))
            continue
        doc = DM.Document(name)
        for f in feats:
            doc.add(f)
        try:
            doc.build()
            out, warns = CE.build_catia_features(doc)
        except Exception as e:
            rows.append((name, text, 0, ['导出异常: %s' % e]))
            continue
        note = zrange_note(doc, out)
        rows.append((name, text, len(out), warns + ([note] if note else [])))

    good = [r for r in rows if r[2] > 0]
    bad = [r for r in rows if r[2] == 0]
    # 这些是**原理上**表达不了的(螺旋线需要 CATIA 的螺旋+扫掠), 不算缺陷:
    # 它们的描述依旧能建模、导出 STL, 只是不能在 CATIA 里用 草图+凸台 复原。
    KNOWN = ('螺旋弹簧', '锥形弹簧')
    real_bad = [r for r in bad if r[0] not in KNOWN]
    known_bad = [r for r in bad if r[0] in KNOWN]

    print('')
    print('  CATIA 导出覆盖自检 —— 零件库共 %d 类' % len(rows))
    print('  ' + '-' * 62)
    if verbose:
        for name, text, n, warns in rows:
            mark = 'OK  ' if n else '缺  '
            print('  %s %-12s -> %2d 个操作   %s' % (mark, name, n, text[:46]))
            if not n and warns:
                print('       ! %s' % warns[0][:70])
    print('  可导出: %d / %d' % (len(good), len(rows)))
    if known_bad:
        print('  已知限制(弹簧类需要 CATIA 的螺旋+扫掠, 非缺陷): %s'
              % ', '.join(r[0] for r in known_bad))
    if real_bad:
        print('  不该失败却失败的: %s' % ', '.join(r[0] for r in real_bad))
    print('  （不能导出的零件描述仍可建模、导出 STL, 或用 STL 导入 CATIA）')
    print('  ' + '-' * 62)
    return 0 if not real_bad else 1


if __name__ == '__main__':
    sys.exit(main())
