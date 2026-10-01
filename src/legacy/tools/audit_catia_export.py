# -*- coding: utf-8 -*-
r"""导出审计: 把"看得见但量不出来"的问题一次性扫出来。

这里查的是**不变量**，而不是"有没有报错"：
  1. 每个零件都至少能导出 1 个操作;
  2. 凸台的 z 范围必须和几何内核算出来的一致（不一致 = 模型位置错了）;
  3. **所有切除必须落在材料"上方"**  —— CATIA 的凹槽朝 -Z 切, 草图如果在材料下方
     就是在空气里切: 不报错、操作数也对, 但模型上什么都没有（踩过这个大坑）;
  4. 切除的深度要够, 能切到材料底部;
  5. 解析的写法容差: 同一件事用逗号/空格/缺参数等不同写法, 特征数应当一致。

用法:
    python tools\audit_catia_export.py          全部审计
    python tools\audit_catia_export.py -v       打印每个用例的细节
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


def specs_all():
    out = {}
    for tbl in (partlib.PARTS, getattr(partlib, 'MEDIUM_PARTS', {}),
                getattr(partlib, 'HARD_PARTS', {})):
        out.update(tbl)
    return out


def phrase_of(name, spec, sample):
    params = spec.get('params') or {}
    bits = []
    for key, value in (sample or {}).items():
        al = params.get(key) or []
        if not al:
            continue
        if isinstance(value, (list, tuple)):
            value = value[0]
        if isinstance(value, float) and abs(value - round(value)) < 1e-9:
            value = int(round(value))
        bits.append('%s%s' % (al[0], value))
    return (name + ' ' + ' '.join(bits)).strip()


def audit_doc(doc):
    """返回问题列表（空 = 通过）。"""
    issues = []
    try:
        feats, _warns = CE.build_catia_features(doc)
    except Exception as e:
        return ['导出抛异常: %s' % e]
    if not feats:
        # 螺旋类(弹簧)原理上就无法用 草图+凸台 表达, 属于已知限制
        helix_only = all(f.kind in ('helix', 'spring', 'taper_spring')
                         for f in doc.features if f.enabled)
        if helix_only and doc.features:
            return []
        return ['一个操作都没有']
    try:
        bb = K.bbox(doc.build())
    except Exception as e:
        return ['内核建模失败: %s' % e]

    pads = [e for e in feats if str(e['kind']).endswith('_pad')]
    cuts = [e for e in feats if str(e['kind']).endswith('_cut')]

    if bb and pads:
        bot = min(e['z'] for e in pads)
        top = max(e['z'] + e['h'] for e in pads)
        if abs(bot - bb[2]) > 1.0 or abs(top - bb[5]) > 1.0:
            issues.append('凸台 z 范围 %.1f~%.1f 与内核 %.1f~%.1f 不一致'
                          % (bot, top, bb[2], bb[5]))
        for c in cuts:
            if c['z'] < top - 1.0:
                issues.append('切除「%s」草图 z=%.1f 落在材料顶面 %.1f 之下 -> 会空切'
                              % (c.get('name') or c['kind'], c['z'], top))
            floor = c['z'] - c['depth']
            if floor >= top - 0.5:
                issues.append('切除「%s」只切到 z=%.1f, 材料顶面在 %.1f -> 根本没切掉材料'
                              % (c.get('name') or c['kind'], floor, top))
            elif c.get('through') and floor > bot + 1.0:
                issues.append('通孔「%s」深度不足: 只能切到 z=%.1f, 材料底部在 %.1f'
                              % (c.get('name') or c['kind'], floor, bot))
    elif cuts and not pads:
        issues.append('只有切除没有材料: %s' % [c['kind'] for c in cuts])

    # 静态检查: 矩形/异形切除必须有可画的轮廓, 否则 VBS 里画出来是退化的
    for c in cuts:
        if c['kind'] in ('rect_cut', 'poly_cut') and not (c.get('profile') or []):
            if (c.get('l') or 0) <= 0 or (c.get('w') or 0) <= 0:
                issues.append('凹槽「%s」没有尺寸(l=%s, w=%s) -> 草图退化'
                              % (c.get('name') or c['kind'], c.get('l'), c.get('w')))
    return issues


def parse_case(text):
    res = nlparser.parse(text)
    if not res.ok:
        return None, ['解析失败: %s' % res.message]
    doc = res.doc
    return doc, []


def main():
    verbose = '-v' in sys.argv
    fails = []
    total = 0

    print('')
    print('  一、零件库全部零件：解析 + 导出 + 不变量')
    print('  ' + '-' * 62)
    specs = specs_all()
    samples = getattr(partlib, 'TYPICAL', {})
    for name, spec in specs.items():
        sample = samples.get(name)
        if not sample:
            continue
        text = phrase_of(name, spec, sample)
        total += 1
        doc, err = parse_case(text)
        if err:
            fails.append((name, text, err))
            continue
        issues = audit_doc(doc)
        if issues:
            fails.append((name, text, issues))
        elif verbose:
            print('  ✅ %-12s %s' % (name, text[:52]))

    print('')
    print('  二、常用描述：解析 + 导出 + 不变量')
    print('  ' + '-' * 62)
    basics = [
        ('画一个 100×60×20 的长方体, 中心打一个直径 10 的通孔', 2),
        ('画一个 100×60×20 的长方体 中心打一个直径 10 的通孔', 2),
        ('底板 200×150×12, 四角各打一个直径 8 的通孔, 内缩 12', 5),
        ('底板 200×150×12 四角各打一个直径8的通孔 内缩12', 5),
        ('法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120', 8),
        ('直径 60 高 120 的圆柱', 1),
        ('齿轮 模数3 齿数24 厚20, 中心孔直径20', 2),
        ('画一个 120×80×15 的长方体, 四角圆角 R8, 在中心打一个直径 20 的通孔', 2),
        ('画一个120×80×15的长方体 四角圆角R8 在中心打一个直径20的通孔', 2),
        ('阶梯轴 直径 40 长 60, 直径 30 长 50, 直径 20 长 40', 3),
        ('套筒 外径 80 内径 50 长 100', 2),
        ('长方体 100×60×20 顶面凹槽 30×20 深5', 2),
        ('长方体 100×60×20 顶面键槽 20×8 深4', 2),
        ('三角转子 外接圆半径60 厚度40 中心孔24', 2),
    ]
    for text, expect in basics:
        total += 1
        doc, err = parse_case(text)
        if err:
            fails.append(('(常用)', text, err))
            continue
        issues = audit_doc(doc)
        feats, _w = CE.build_catia_features(doc)
        if len(feats) != expect:
            issues.append('操作数 %d, 期望 %d' % (len(feats), expect))
        if issues:
            fails.append(('(常用)', text, issues))
        elif verbose:
            print('  ✅ %-2d 个操作  %s' % (len(feats), text[:56]))

    print('')
    print('  三、写法容差：同一件事换个写法，特征数应当一致')
    print('  ' + '-' * 62)
    variants = [
        ('齿轮+中心孔', ['齿轮 模数3 齿数24 厚20, 中心孔直径20',
                     '齿轮 模数3 齿数24 厚20 中心孔直径20',
                     '齿轮 模数3 齿数24 厚20，中心孔直径20',
                     '直齿圆柱齿轮 模数3 齿数24 齿宽20 中心孔直径20',
                     '直齿圆柱齿轮 模数2 齿数30 轮毂长度30, 轮毂外径36, 分度圆直径60, '
                     '齿顶圆直径64, 齿根圆直径55, 中心孔直径15',
                     '直齿圆柱齿轮 模数2 齿数30 轮毂长度30 轮毂外径36 分度圆直径60 '
                     '齿顶圆直径64 齿根圆直径55 中心孔直径15',
                     '齿轮 模数2 齿数30 厚20 中心孔15'], 2),
        ('法兰+螺栓孔', ['法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120',
                     '法兰 外径160 厚20 内径60 均布6个直径12的螺栓孔 分度圆120'], 8),
        ('底板四角孔', ['底板 200×150×12, 四角各打一个直径 8 的通孔, 内缩 12',
                    '底板 200×150×12 四角各打一个直径8的通孔 内缩12'], 5),
        ('长方体+凹槽', ['长方体 100×60×20 顶面凹槽 30×20 深5',
                     '长方体 100×60×20 凹槽 30×20 深5',
                     '长方体 100×60×20, 顶面凹槽 30×20, 深 5'], 2),
        ('三角转子+中心孔', ['三角转子 外接圆半径60 厚度40 中心孔24',
                       '三角转子 外接圆半径60 厚度40, 中心孔直径24'], 2),
    ]
    for label, texts, expect in variants:
        counts = []
        for text in texts:
            total += 1
            doc, err = parse_case(text)
            if err:
                fails.append((label, text, err))
                continue
            feats, _w = CE.build_catia_features(doc)
            counts.append(len(feats))
            issues = audit_doc(doc)
            if issues:
                fails.append((label, text, issues))
            elif verbose:
                print('  ✅ %-12s %-2d 个操作  %s' % (label, len(feats), text[:44]))
        if counts and any(c != expect for c in counts):
            fails.append((label, '／'.join(t[:22] for t in texts),
                          ['写法容差: 特征数 %s, 期望都等于 %d' % (counts, expect)]))

    print('')
    print('  ' + '=' * 62)
    print('  共审计 %d 个用例, 失败 %d 个' % (total, len(fails)))
    for name, text, issues in fails:
        print('')
        print('  ❌ %s' % name)
        print('     描述: %s' % text)
        for it in (issues if isinstance(issues, list) else [issues]):
            print('     - %s' % it)
    print('  ' + '=' * 62)
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
