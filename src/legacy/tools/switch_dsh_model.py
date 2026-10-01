# -*- coding: utf-8 -*-
r"""切换 DSH (DeepSeek Harness) 使用的模型。

用法:
    python tools\switch_dsh_model.py                 # 交互式选择
    python tools\switch_dsh_model.py --list          # 只列出可选模型
    python tools\switch_dsh_model.py --model deepseek-v4-flash
    python tools\switch_dsh_model.py --restore       # 还原到最近一次备份

改完需要**新开一个会话**才会生效, 当前会话不受影响。
每次修改前都会自动备份到 settings.yaml.bak-时间戳。
"""
from __future__ import annotations

import argparse
import datetime
import glob
import os
import shutil
import sys

SETTINGS = os.path.join(os.path.expanduser('~'), '.dsh', 'settings.yaml')

# 依据 DeepSeek 官方「模型 & 价格」页 (api-docs.deepseek.com) 整理。
# 关键: DeepSeek-V4.1-Flash 的**正式 API 模型名是 deepseek-flash**;
#       旧的 deepseek-v4-flash / deepseek-v4-flash-vision-exp 仍可调用,
#       但它们对应的模型已下线, 请求会被路由到 DeepSeek-V4.1-Flash 并按其计费
#       —— 也就是说这几个名字实际拿到的是同一个模型。
MODELS = [
    ('deepseek-flash',               'DeepSeek-V4.1-Flash   当前正式名 (推荐)'),
    ('deepseek-v4-pro',              'DeepSeek-V4-Pro-0813  能力最强 · 不支持图像 · 约 4.5 倍价'),
    ('deepseek-v4-flash',            '旧名, 实际仍是 V4.1-Flash'),
    ('deepseek-v4-flash-vision-exp', '旧名, 实际仍是 V4.1-Flash'),
    ('deepseek-chat',                '兼容别名 → 当前主力对话模型'),
    ('deepseek-reasoner',            '兼容别名 → 当前推理模型'),
]


def say(m=''):
    print(m, flush=True)


def read_settings():
    if not os.path.isfile(SETTINGS):
        return None
    with open(SETTINGS, 'r', encoding='utf-8') as f:
        return f.read()


def current_model(text):
    for line in (text or '').splitlines():
        s = line.strip()
        if s.startswith('model:'):
            return s.split(':', 1)[1].strip()
    return None


def set_model(text, model):
    out = []
    hit = False
    for line in text.splitlines():
        if line.strip().startswith('model:'):
            indent = line[:len(line) - len(line.lstrip())]
            out.append('%smodel: %s' % (indent, model))
            hit = True
        else:
            out.append(line)
    if not hit:
        raise SystemExit('settings.yaml 里没有 model: 这一行, 请手动检查')
    return '\n'.join(out) + '\n'


def backup(path):
    bak = path + '.bak-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    shutil.copyfile(path, bak)
    return bak


def restore_latest():
    baks = sorted(glob.glob(SETTINGS + '.bak-*'))
    if not baks:
        say('没有找到备份')
        return 1
    latest = baks[-1]
    shutil.copyfile(latest, SETTINGS)
    say('已还原: %s  <-  %s' % (SETTINGS, os.path.basename(latest)))
    say('当前模型: %s' % current_model(read_settings()))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--show', action='store_true')
    args = ap.parse_args()

    text = read_settings()
    if text is None:
        say('找不到 %s' % SETTINGS)
        say('请先安装并运行一次 dsh。')
        return 1

    if args.show:
        say('配置文件 : %s' % SETTINGS)
        say('当前模型 : %s' % current_model(text))
        return 0

    if args.restore:
        return restore_latest()

    if args.list:
        say('可用模型:')
        for mid, desc in MODELS:
            say('  %-30s %s' % (mid, desc))
        say('')
        say('当前: %s' % current_model(text))
        return 0

    model = args.model
    if not model:
        say('')
        say('  DSH 模型切换')
        say('  ' + '-' * 58)
        for i, (mid, desc) in enumerate(MODELS, 1):
            say('   %d) %-30s %s' % (i, mid, desc))
        say('   0) 还原到最近一次备份')
        say('')
        say('  当前模型: %s' % current_model(text))
        say('')
        try:
            ch = input('  请输入序号 (直接回车=%s): ' % MODELS[0][0]).strip()
        except EOFError:
            ch = ''
        if ch == '0':
            return restore_latest()
        if ch == '':
            model = MODELS[0][0]
        else:
            try:
                model = MODELS[int(ch) - 1][0]
            except (ValueError, IndexError):
                say('输入无效')
                return 1

    old = current_model(text)
    if old == model:
        say('当前已经是 %s, 无需修改' % model)
        return 0
    bak = backup(SETTINGS)
    with open(SETTINGS, 'w', encoding='utf-8') as f:
        f.write(set_model(text, model))
    say('')
    say('  已切换: %s  ->  %s' % (old, model))
    say('  备份  : %s' % bak)
    say('')
    say('  **需要新开一个会话才会生效** (当前会话仍用旧模型)。')
    say('')
    say('  注意: DSH 正在运行时可能会把配置覆盖回去。最稳妥的做法是')
    say('        直接在 DSH 界面的模型下拉框里切换。')
    say('  想还原: python tools/switch_dsh_model.py --restore')
    return 0


if __name__ == '__main__':
    sys.exit(main())
