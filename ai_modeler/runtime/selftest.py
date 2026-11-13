# -*- coding: utf-8 -*-
"""内置自检: python -m ai_modeler --selftest"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import os
import sys
import tempfile
import time

from ai_modeler.core import exporters, kernel as K
from ai_modeler.core import docmodel as DM
from ai_modeler.core import nlparser as NLP


def run_selftest(verbose: bool = True) -> int:
    ok = [0, 0]

    def check(name, cond, extra=''):
        ok[1] += 1
        if cond:
            ok[0] += 1
        if verbose:
            print('[%s] %-34s %s' % ('PASS' if cond else 'FAIL', name, extra))

    print('AI 自动建模助手 内置自检')
    print('Python %s' % sys.version.split()[0])
    print('-' * 62)

    # 1. 几何内核
    t0 = time.time()
    box = K.make_box(100, 60, 20, (0, 0, 10))
    check('内核: 创建长方体', len(box) == 8, '%d 面' % len(box))
    check('内核: 体积正确', abs(K.volume_of(box) - 120000) < 1, '%.1f mm3' % K.volume_of(box))
    cyl = K.make_cylinder(5, 40, (0, 0, 10), 48)
    cut = K.csg_subtract(box, cyl)
    v = K.volume_of(cut)
    check('内核: 布尔差集', abs(v - (120000 - 3.14159265 * 25 * 20)) < 200, '%.1f mm3' % v)
    uni = K.csg_union(box, K.make_box(40, 40, 40, (60, 0, 20)))
    check('内核: 布尔并集', len(uni) > 6, '%d 面' % len(uni))
    check('内核: 性能', (time.time() - t0) < 5.0, '%.2fs' % (time.time() - t0))

    # 2. 自然语言
    cases = [
        ('画一个 100×60×20 的长方体', 1),
        ('100×20 毫米的矩形草图, 然后拉伸 20 毫米', 1),
        ('画一个 120×80×15 的长方体, 四角圆角 R8, 中心打一个直径 20 的通孔', 2),
        ('底板 200×150×12, 四角各打一个直径 8 的通孔, 内缩 12', 2),
        ('法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120', 3),
        ('阶梯轴 直径 40 长 60, 直径 30 长 50, 直径 20 长 40', 3),
        ('叶盘 外径 220 厚 30 叶片数 24 叶展 45', 3),
        ('齿轮 模数 3 齿数 24 厚 20, 中心孔直径 20', 2),
        ('套筒 外径 80 内径 50 长 100', 2),
    ]
    for (text, expect) in cases:
        t = time.time()
        res = NLP.parse(text)
        n = len(res.doc.features)
        built = False
        try:
            built = len(res.doc.build()) > 0
        except Exception:
            built = False
        check('中文解析: ' + text[:16], res.ok and n == expect and built,
              '特征 %d/%d, %.2fs' % (n, expect, time.time() - t))

    # 2b. 回归用例: 这些都是曾经真实出错的说法
    regress = [
        ('圆台底径顶径', '圆台 底径 80 顶径 30 高 60', 1, (80, 80, 60)),
        ('回转角度作为修饰', '回转体 半径 40 高 60, 回转 360 度', 1, (80, 80, 60)),
        ('阵列不被分度圆劫持', '直径60高10的圆柱, 中心打一个直径6的通孔, 绕中心圆周阵列 8 个',
         3, (60, 60, 10)),
        ('带空格的沿X方向阵列', '画一个20×20×10的长方体, 沿 X 方向阵列 4 个, 间距 40',
         2, (140, 20, 10)),
    ]
    for (name, text, n_exp, size_exp) in regress:
        res = NLP.parse(text)
        doc = res.doc
        size = None
        n = 0
        try:
            doc.build()
            n = len(doc.features)
            st = doc.stats()
            size = tuple(round(v, 1) for v in st['size']) if st['size'] else None
        except Exception as e:
            check('回归: ' + name, False, str(e))
            continue
        good = (n == n_exp) and size is not None and all(
            abs(a - b) <= max(0.6, b * 0.06) for a, b in zip(size, size_exp))
        check('回归: ' + name, good, '特征 %d/%d 尺寸 %s' % (n, n_exp, size))

    # 3. 导出
    doc = NLP.parse('画一个 60×40×20 的长方体, 中心打一个直径 10 的通孔').doc
    body = doc.build()
    root = str(PROJECT_ROOT)
    tmp = os.path.join(root, 'artifacts', 'selftest', time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(tmp, exist_ok=True)
    p1 = exporters.export_stl(body, os.path.join(tmp, 't.stl'), 't')
    p2 = exporters.export_obj(body, os.path.join(tmp, 't.obj'), 't')
    p3 = exporters.export_project(doc, os.path.join(tmp, 't.aimodel'))
    p4 = exporters.export_report(doc, os.path.join(tmp, 't.txt'), 'selftest')
    p5 = exporters.export_catia_script(doc, os.path.join(tmp, 't_catia.py'))
    check('导出: STL', os.path.getsize(p1) > 84, '%d 字节' % os.path.getsize(p1))
    check('导出: OBJ', os.path.getsize(p2) > 20, '%d 字节' % os.path.getsize(p2))
    check('导出: 工程文件', os.path.getsize(p3) > 20, '%d 字节' % os.path.getsize(p3))
    check('导出: 建模报告', os.path.getsize(p4) > 20, '%d 字节' % os.path.getsize(p4))
    check('导出: CATIA 脚本', os.path.getsize(p5) > 20, '%d 字节' % os.path.getsize(p5))

    # 4. STL 合法性
    with open(p1, 'rb') as f:
        head = f.read(84)
    import struct
    ntris = struct.unpack('<I', head[80:84])[0] if len(head) >= 84 else 0
    check('STL: 三角面片数', ntris > 0, '%d 面' % ntris)
    check('STL: 文件长度一致', os.path.getsize(p1) == 84 + ntris * 50,
          '%d == %d' % (os.path.getsize(p1), 84 + ntris * 50))

    # 5. 工程往返
    d2 = DM.Document.from_dict(doc.to_dict())
    check('工程: 序列化往返', len(d2.features) == len(doc.features)
          and abs(d2.stats()['volume'] - doc.stats()['volume']) < 1e-6)

    # 6. MCP
    try:
        from ai_modeler.runtime import mcp_server
        tools = mcp_server.list_tools()
        check('MCP: 工具注册', len(tools) >= 15, '%d 个工具' % len(tools))
        r = mcp_server.handle_request({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                       'params': {'protocolVersion': '2024-11-05'}})
        check('MCP: initialize', r and 'result' in r, r['result']['serverInfo']['name'])
        r = mcp_server.handle_request({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                       'params': {'name': 'build_from_text',
                                                  'arguments': {'text': '画一个 50×50×10 的长方体'}}})
        txt = r['result']['content'][0]['text']
        check('MCP: 调用建模工具', '长方体' in txt and r['result']['isError'] is False)
    except Exception as e:
        check('MCP: 服务端', False, str(e))

    # 7. CATIA 探测
    try:
        from ai_modeler.catia_backend import catia
        avail, msg = catia.probe()
        if verbose:
            print('[INFO] %-34s %s' % ('CATIA V5', msg if not avail else '已检测到'))
    except Exception as e:
        check('CATIA: 驱动探测', False, str(e))

    # 8. CATIA 导出
    try:
        import py_compile
        from ai_modeler.catia_backend import catia_export
        d = NLP.parse('法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120').doc
        d.build()
        feats, warns = catia_export.build_catia_features(d)
        check('CATIA: 特征展开', len(feats) == 8, '%d 个 CATIA 操作' % len(feats))
        p = catia_export.export_catia_script(d, os.path.join(tmp, 'catia_part.py'))
        try:
            py_compile.compile(p, doraise=True)
            check('CATIA: 脚本语法合法', True, '%d 字节' % os.path.getsize(p))
        except Exception as e:
            check('CATIA: 脚本语法合法', False, str(e))
        v = catia_export.export_catia_vbs(d, os.path.join(tmp, 'catia_feat.vbs'))
        nrec = 0
        with open(v, 'r', encoding='utf-16') as fh:
            for line in fh:
                if line.startswith('FEAT_') and '"' in line:
                    nrec += 1
        check('CATIA: VBS 特征表', nrec == 8, '%d 条记录' % nrec)
        # 重复切除的合并规则（实测教训：型材 13 个实测孔被合并掉 3 个 Z 向孔）
        _e = catia_export._entry
        _dup = [_e(kind='circle_cut', plane='Z', cx=0.0, cy=5.32, r=12.0, depth=10.0),
                _e(kind='circle_cut', plane='Z', cx=0.0, cy=0.0, r=12.0, depth=10.0)]
        _cross = [_e(kind='circle_cut', plane='X', cx=0.0, cy=0.0, r=5.5, depth=80.0),
                  _e(kind='circle_cut', plane='Z', cx=0.0, cy=0.0, r=5.5, depth=80.0),
                  _e(kind='circle_cut', plane='Y', cx=0.0, cy=0.0, r=5.5, depth=80.0)]
        _grid = [_e(kind='circle_cut', plane='X', cx=0.0, cy=0.0, r=5.5, depth=80.0),
                 _e(kind='circle_cut', plane='X', cx=0.0, cy=15.0, r=5.5, depth=80.0)]
        _k1, _d1 = catia_export._dedupe_cuts(_dup)
        _k2, _d2 = catia_export._dedupe_cuts(_cross)
        _k3, _d3 = catia_export._dedupe_cuts(_grid)
        check('CATIA: 重复切除合并规则',
              _d1 == 1 and len(_k1) == 1 and _d2 == 0 and len(_k2) == 3
              and _d3 == 0 and len(_k3) == 2,
              '同轴重复合 1；交叉孔保留 3；15mm 孔阵保留 2')
        # 用 Windows 脚本宿主整份编译宏 (cscript 只认 .vbs 扩展名, 故先复制)
        macro = os.path.join(str(PROJECT_ROOT),
                             'catia_plugin', 'AI自动建模助手.CATScript')
        if os.path.isfile(macro):
            import shutil
            import subprocess as _sp
            probe = os.path.join(tmp, 'macro_syntax_check.vbs')
            shutil.copyfile(macro, probe)
            cp = _sp.run(['cscript', '//nologo', probe], capture_output=True, text=True,
                         timeout=60)
            check('CATIA: 宏编译通过', cp.returncode == 0,
                  ((cp.stdout or '') + (cp.stderr or '')).strip()[:80])
        else:
            check('CATIA: 宏编译通过', False, '未安装插件')
    except Exception as e:
        check('CATIA: 导出模块', False, str(e))

    # 9. 界面模块
    try:
        import tkinter  # noqa: F401
        from ai_modeler.ui import app as _app  # noqa: F401
        from ai_modeler.ui import viewer3d as _v  # noqa: F401
        check('界面: 模块可加载', True)
    except Exception as e:
        check('界面: 模块可加载', False, str(e))

    # 10. 夹具单元协同设计五步工作流
    # 这一组检查守的是研制报告里那几条红线：前置不满足不能进下一步、
    # 固定基准不能动、参数一变下游结论作废、未实现的检查不许报通过。
    try:
        from ai_modeler.workflow import (
            STAGES, WorkflowEngine, build_demo_project, evaluate)
        _proj = build_demo_project()
        check('工作流: 五阶段定义', len(STAGES) == 5, '%d 阶段' % len(STAGES))

        _checks = evaluate(_proj)
        _pass = sum(1 for c in _checks if c.status == 'pass')
        _fail = sum(1 for c in _checks if c.status == 'fail')
        check('工作流: 规则校验可运行', len(_checks) >= 7,
              '%d 项 (%d 通过 / %d 未通过)' % (len(_checks), _pass, _fail))

        # 四分法要在任何作废动作**之前**取样：invalidate_from 是原地改对象，
        # 先作废再统计就只剩 not_run，等于什么都没验到。
        _st = {c.status for c in _checks}
        check('工作流: 检查结论四分法', _st <= {'pass', 'fail', 'not_run', 'not_applicable'},
              ' / '.join(sorted(_st)))

        # 默认案例的压臂间距故意不足。走到第 4 阶段后再看第 5 阶段的门禁，
        # 报出的必须是「检查未通过」而不是「资料没填」——这正是研制报告
        # 5.2「禁止进入复核」那条要求落到代码上的样子。
        # 用独立实例跑：commit 会写 history、改 stage，别污染后面要用的 _proj。
        _gate = WorkflowEngine(build_demo_project())
        for _stage in (1, 2, 3, 4):
            _gate.commit(_stage)
        _enter, _miss = _gate.can_enter(5)
        check('工作流: 检查未通过禁止进复核',
              (not _enter) and any('检查未通过' in str(m) for m in _miss),
              (str(_miss[0])[:46] if _miss else '没有被拦下'))

        # 结论必须真正挂在项目上才能谈"失效"：先把检查结果落到 project.checks，
        # 再改参数，看下游是不是真的被作废——否则这条检查只是空跑。
        _eng = WorkflowEngine(_proj)
        _proj.checks = list(_checks)
        _voided = _eng.invalidate_from(1)
        check('工作流: 参数变更使下游失效', _voided >= 1,
              '%d 项检查被作废' % _voided)

        # 报告里的「默认失败 → 接受修正后通过」分支必须真的复现得出来
        _eng.set_param('clamp_arm', 'length', 75.0)
        _fixed = [c for c in evaluate(_proj) if c.check_id == 'W-ARM-01']
        check('工作流: 修正后压臂间距转通过',
              bool(_fixed) and _fixed[0].status == 'pass',
              _fixed[0].detail[:40] if _fixed else '未产生 W-ARM-01')
    except Exception as e:
        check('工作流: 五步引擎', False, str(e))

    try:
        from ai_modeler.ui import fixture_workflow as _fw  # noqa: F401
        check('界面: 五步向导可加载', True)
    except Exception as e:
        check('界面: 五步向导可加载', False, str(e))

    # 11. 元增量学习
    # 守的是这套设计的命门：空库不能炸、学完要能召回、没验证的样本不许进原型。
    try:
        from ai_modeler.meta import MetaMemory, load_dataset
        _mem = MetaMemory(None)                      # 内存模式，不碰磁盘
        _cold = _mem.suggest('内六角螺栓 M5x8')
        check('元增量: 冷启动不炸', _cold.get('family') is None,
              '空库返回 None 而不是抛异常')

        _rows = load_dataset()
        check('元增量: 训练集可读', len(_rows) >= 1400,
              '%d 条（已排除无族标签的负例）' % len(_rows))

        _mem.observe_batch(_rows[:200], verified=True)
        _hit = _mem.suggest('内六角螺栓 M5x8')
        check('元增量: 学习后能召回', bool(_hit.get('family')),
              '预测=%s 置信度=%.2f' % (_hit.get('family'),
                                      float(_hit.get('confidence') or 0)))

        _before = int(_mem.stats().get('samples') or 0)
        _mem.observe('这是一条没验证过的样本 XX-99', '内六角螺钉', 'M9x99',
                     None, verified=False)
        check('元增量: 写入门禁', int(_mem.stats().get('samples') or 0) == _before,
              '未验证样本不进原型、不涨计数')
    except Exception as e:
        check('元增量: 核心模块', False, str(e))

    try:
        from ai_modeler.ui import meta_panel as _mp  # noqa: F401
        check('界面: 元增量面板可加载', True)
    except Exception as e:
        check('界面: 元增量面板可加载', False, str(e))

    print('-' * 62)
    print('通过 %d / %d' % (ok[0], ok[1]))
    print('临时导出目录: %s' % tmp)
    return 0 if ok[0] == ok[1] else 1


if __name__ == '__main__':
    sys.exit(run_selftest())
