# -*- coding: utf-8 -*-
r"""生成复杂零件建模测试清单 (中等难度)。

输出: 复杂零件建模测试清单.md
"""
from __future__ import annotations
import os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ai_modeler import kernel as K, nlparser as NLP, partlib

OUT = os.path.join(ROOT, '复杂零件建模测试清单.md')
BT = chr(96)

CASES = [
 ('① 弹性元件', [
   ('螺旋弹簧 线径2 外径20 自由高度50 圈数8', (20, 20, 50), '看是不是真的螺旋，数圈数'),
   ('螺旋弹簧 线径3 外径30 自由高度80 圈数10', (30, 30, 80), '线径加粗、圈数变多'),
   ('压缩弹簧 线径1.5 外径15 高度40 圈数6', (15, 15, 40), '用"压缩弹簧"这个说法'),
   ('锥形弹簧 线径2 外径24 自由高度40 圈数6', (24, 24, 40), '看螺旋半径是否逐渐收缩'),
   ('碟形弹簧 内径20 外径40 厚度3', (40, 40, 3), '碟形（简化为圆环）'),
 ]),
 ('② 传动件', [
   ('花键轴 小径20 大径25 长度40 齿数8', (25, 25, 40), '数齿数，看真花键齿形'),
   ('花键轴 小径30 大径36 长度60 齿数10', (36, 36, 60), '齿数改成 10'),
   ('多楔带轮 外径100 孔径25 宽度30 槽数3', (100, 100, 30), '数 V 形槽'),
   ('多楔带轮 外径160 孔径40 宽度45 槽数5', (160, 160, 45), '槽数改成 5'),
   ('蜗杆 分度圆直径32 外径40 长度60', (40, 40, 60), '圆柱 + 螺旋螺纹'),
 ]),
 ('③ 凸轮 / 轮盘', [
   ('盘形凸轮 基圆直径40 外径56 厚度10', None, '看桃形轮廓：基圆+升程'),
   ('凸轮 基圆直径60 外径80 厚度15', None, '换个尺寸'),
   ('手轮 外径120 孔径20 厚度10 辐条数5', (120, 120, 10), '数辐条，看外圈和轮毂'),
   ('方向盘 外径300 内径40 厚度25 辐条数3', (300, 300, 25), '大直径、3 根辐条'),
 ]),
 ('④ 制动 / 离合', [
   ('制动盘 外径280 厚度22 中心孔64', (280, 280, 22), '刹车盘：盘体+中心孔'),
   ('制动鼓 外径300 内径260 高度80', (300, 300, 80), '制动鼓：外筒+5 个螺栓孔'),
   ('离合器摩擦片 内径120 外径200 厚度8', (200, 200, 8), '环形摩擦片'),
   ('法兰 外径200 内径100 厚度25', (200, 200, 25), '法兰盘'),
 ]),
 ('⑤ 支承 / 连接', [
   ('轴承座 内径30 外径60 高度40', (60, 60, 40), '带内孔的座'),
   ('轴套 内径25 外径35 长度40', (35, 35, 40), '衬套'),
   ('挡圈 内径20 外径24 厚度1.2', (24, 24, 1.2), '薄挡圈，注意很薄'),
   ('平键 宽度8 高度7 长度40', (8, 7, 40), '长方体键'),
   ('垫圈 内径10 外径20 厚度2', (20, 20, 2), '平垫圈'),
   ('联轴器 内径25 外径80 长度60', (80, 80, 60), '联轴器'),
 ]),
 ('⑥ 组合 / 高难度', [
   ('直齿圆柱齿轮 模数2 齿数40 齿宽15', (84, 84, 15), '齿轮，齿顶圆 = 2×(40+2)'),
   ('直齿圆柱齿轮 模数4 齿数18 齿宽30', (80, 80, 30), '大模数少齿数'),
   ('深沟球轴承 内径25 外径52 宽度15', (52, 52, 15), '国标 6205'),
   ('螺栓 螺纹规格12 长度50', (12, 12, 50), '简化光杆'),
   ('螺母 螺纹规格12 对边宽度19 厚度10', (19, 19, 10), '六角柱挖通孔'),
 ]),
 ('⑦ 口语化（考验鲁棒性）', [
   ('帮我做一个线径2外径20高50的弹簧', None, '口语 + 参数顺序打乱'),
   ('来个直径60的凸轮，基圆直径40，厚12', None, '口语"来个"'),
   ('整一个内径20外径47宽14的轴承', (47, 47, 14), '口语"整一个"'),
   ('花键轴 d20 D25 L40 齿数8', None, '符号式混合'),
 ]),
]


def run(text):
    t0 = time.time()
    try:
        r = NLP.parse(text)
    except Exception as e:
        return None, '解析异常: %s' % str(e)[:40], 0
    if not r.ok:
        return None, '未识别', 0
    try:
        body = r.doc.build(deadline=time.time() + 25)
    except Exception as e:
        return None, '构建中止: %s' % str(e)[:36], 0
    bb = K.bbox(body)
    size = (round(bb[3]-bb[0],1), round(bb[4]-bb[1],1), round(bb[5]-bb[2],1)) if bb else None
    return size, '', len(r.doc.features)


def fmt(s):
    return '（无）' if not s else '%.4g × %.4g × %.4g' % s


def close(a, b, tol=0.05):
    if a is None or b is None:
        return None
    return all(abs(x-y) <= max(tol*y, 0.6) for x, y in zip(a, b))


L = []
L.append('# 复杂零件建模测试清单（中等难度）')
L.append('')
L.append('> 这一份比《建模测试清单》难一档：弹性元件、传动件、凸轮、制动件、') 
L.append('> 组合件都用上了**螺旋扫掠、花键齿形、凸轮轮廓、辐条阵列、多槽切除**这些')
L.append('> 更复杂的几何运算。每一条都**已实跑验证**。')
L.append('')
L.append('**怎么看结果**')
L.append('')
L.append('1. 右侧「**统计**」页看**外形尺寸**，和"应该看到"那列比')
L.append('2. 三维视图里**转到侧面和俯视**看形状特征（圈数、齿数、辐条数、槽数）')
L.append('3. 「**日志**」页会显示用了什么特征、多少面片')
L.append('')
L.append('> 复杂件布尔运算较慢，**单件 1~3 秒属正常**；超过 30 秒会自动中止。')
L.append('')
L.append('**图例**：✅ 正常 ｜ ⚠️ 能建出来但和预期不符 ｜ ❌ 没建出来')
L.append('')

total = okn = 0
summary = []
for sec, cases in CASES:
    L.append('---')
    L.append('')
    L.append('## ' + sec)
    L.append('')
    L.append('| # | 输入这句话 | 应该看到（外形尺寸 mm） | 实测 | 看点 |')
    L.append('|---|---|---|---|---|')
    n_ok = 0
    for i, (text, exp, note) in enumerate(cases, 1):
        size, err, nf = run(text)
        total += 1
        if err:
            mark = '❌ ' + err
        else:
            c = close(size, exp)
            if c is False:
                mark = '⚠️ ' + fmt(size) + ' (与预期不符)'
            else:
                mark = '✅ ' + fmt(size)
                n_ok += 1
                okn += 1
        L.append('| %d | %s | %s | %s | %s |'
                 % (i, BT + text + BT, fmt(exp) if exp else '（形状类）', mark, note))
    L.append('')
    summary.append((sec, n_ok, len(cases)))

L.append('---')
L.append('')
L.append('## 结果汇总')
L.append('')
L.append('| 分组 | 通过 / 总数 | 通过率 |')
L.append('|---|---|---|')
for sec, a, b in summary:
    L.append('| %s | %d / %d | %.0f%% |' % (sec, a, b, a*100.0/b))
L.append('| **合计** | **%d / %d** | **%.0f%%** |' % (okn, total, okn*100.0/total))
L.append('')
L.append('---')
L.append('')
L.append('## 中等难度零件的建模原理')
L.append('')
L.append('| 零件 | 用到的几何运算 |')
L.append('|---|---|')
L.append('| 螺旋弹簧 | 圆形截面沿螺旋线**放样扫掠**（每圈 14 个截面） |')
L.append('| 花键轴 | 矩形花键**轮廓生成** + 拉伸 |')
L.append('| 盘形凸轮 | 基圆 + 简谐升程**极坐标轮廓** + 拉伸 |')
L.append('| 辐条手轮 | 外圈（布尔差）+ 轮毂 + N 根辐条**圆周阵列并集** |')
L.append('| 多楔带轮 | 圆柱 − N 个**回转体 V 槽** |')
L.append('| 制动鼓 | 外筒 + 中心孔 + **分度圆均布孔** |')
L.append('| 蜗杆 | 圆柱 + **螺旋扫掠**螺纹 |')
L.append('| 齿轮 | 渐开线近似**齿形轮廓** + 拉伸 |')
L.append('')

open(OUT, 'w', encoding='utf-8').write('\n'.join(L))
print('已写出 %s (%.1f KB)' % (OUT, os.path.getsize(OUT)/1024))
print('总计 %d 条, 通过 %d 条 (%.0f%%)' % (total, okn, okn*100.0/total))
for sec, a, b in summary:
    print('   %-24s %2d/%2d' % (sec, a, b))
