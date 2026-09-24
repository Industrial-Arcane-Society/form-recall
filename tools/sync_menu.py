# -*- coding: utf-8 -*-
r"""同步助手 —— 一个窗口，两个方向，自动判断该干哪件事。

用户要的："把'推我的改动'和'拉队友的改动'合成一个，点一下打开命令窗口让我选，智能方便快捷有效。"

设计原则：
  1. **打开就先说清状态**：队友有几个新提交？我这边有几个文件改过？
     然后给出**建议动作**（直接回车就能执行，不用想该选哪个）；
  2. **两个方向都真能用**：
       拉取 = 协作包 git pull → 按原目录布局镜像到工作目录 → 自检 35/35；
       推送 = 工作区改动 → 送进协作包 → 打包四项判据 → 自检 → 提交推送；
     （之前"推送"只推协作包里的内容，你在工作目录改的东西是推不上去的，这一步补上了。）
  3. **每一步都有兜底**：拉完自检不过 → 提示回滚；推之前判据不过 → 不推，说清缺什么；
  4. **不猜、不乱动**：只碰真正改过的文件，覆盖前备份，删除前比哈希。

用法：双击 `同步助手.bat`（或 python tools\sync_menu.py）
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import git_sync as GS                                      # noqa: E402
from push_to_github import find_git, github_token, run     # noqa: E402

TOOLS = os.path.dirname(os.path.abspath(__file__))
CHECK_REPORT = "协作包完整性检查.md"
C_OK, C_WARN, C_ERR, C_HEAD, C_DIM, C_END = "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[90m", "\033[0m"


def enable_color():
    if os.name == "nt":
        os.system("")           # 让 Windows 控制台认 ANSI 转义


def hr(ch="─", n=68):
    print(C_DIM + ch * n + C_END)


def run_py(script, args=(), title=None, quiet=False, cwd=None):
    """跑一个项目内的 python 脚本，回显它的输出，返回 (退出码, 输出文本)。"""
    path = os.path.join(TOOLS, script)
    p = subprocess.run([sys.executable, path] + list(args), cwd=cwd or GS.default_work(),
                       capture_output=True)
    out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    if not quiet:
        print(out.rstrip())
    return p.returncode, out


# --------------------------------------------------------------------- 状态
def status():
    """返回一个字典：队友的新提交、我这边的改动、建议动作。"""
    git = find_git()
    pack, work = GS.DEF_PACK, GS.default_work()
    tok, _ = github_token()
    st = {"behind": 0, "behind_list": [], "ws_changed": 0, "ws_new": 0,
          "ws_list": [], "pack_ok": None, "error": None, "git": git,
          "pack": pack, "work": work}

    if not git:
        st["error"] = "找不到 git（装 GitHub Desktop 或 Git for Windows）"
        return st
    if not os.path.isdir(os.path.join(pack, ".git")):
        st["error"] = "协作包不是 git 仓库：%s（跑一次 scripts\\06_初始化Git仓库.ps1）" % pack
        return st
    if tok:
        run(git, ["fetch", "origin", "main"], pack, token=tok, quiet=True)
    rc, head, _ = GS.g(git, ["rev-parse", "HEAD"], pack)
    rc2, remote, _ = GS.g(git, ["rev-parse", "origin/main"], pack)
    if head and remote and head != remote:
        rc3, log, _ = GS.g(git, ["log", "--oneline", "%s..%s" % (head, remote)], pack)
        st["behind_list"] = [l for l in log.splitlines() if l.strip()]
        st["behind"] = len(st["behind_list"])
        rc4, alog, _ = GS.g(git, ["log", "--oneline", "%s..%s" % (remote, head)], pack)
        st["ahead_list"] = [l for l in alog.splitlines() if l.strip()]
        st["ahead"] = len(st["ahead_list"])
    else:
        st["ahead"] = 0
        st["ahead_list"] = []
    try:
        differing, missing, new = GS.scan_out(git, pack, work)
        st["ws_list"] = differing
        st["ws_changed"] = len(differing)
        st["ws_new"] = len(new)
    except Exception as e:
        st["error"] = "扫描工作区失败：%s" % e
    return st


def show_status(st):
    hr()
    print(C_HEAD + "  同步助手 —— 本地工作区 与 GitHub 私有仓库" + C_END)
    print(C_DIM + "  工作区: %s" % st["work"] + C_END)
    print(C_DIM + "  仓库  : %s" % st["pack"] + C_END)
    hr()
    if st.get("error"):
        print(C_ERR + "  [!] %s" % st["error"] + C_END)
        return
    b, w, n = st["behind"], st["ws_changed"], st["ws_new"]
    if b:
        print("  " + C_WARN + "↓ 队友有 %d 个新提交等着你拉" % b + C_END)
        for l in st["behind_list"][:5]:
            print(C_DIM + "      " + l + C_END)
    else:
        print("  " + C_OK + "[OK] 队友没有新东西（已是最新）" + C_END)
    if st.get("ahead"):
        print("  " + C_WARN + "↑ 你本地还有 %d 个提交没推上去" % st["ahead"] + C_END)
    if w or n:
        print("  " + C_WARN + "[改] 你这边改过 %d 个文件，另有 %d 个新文件" % (w, n) + C_END)
        for l in st["ws_list"][:5]:
            print(C_DIM + "      " + l + C_END)
        if w > 5:
            print(C_DIM + "      ... 还有 %d 个" % (w - 5) + C_END)
    else:
        print("  " + C_OK + "[OK] 你这边没有未同步的改动" + C_END)
    hr()
    # 给建议（回车即执行）
    if b and (w or n):
        return "3", "两边都有变化：建议先拉队友的（1），再推自己的（2）"
    if b:
        return "1", "队友有新提交：建议拉取"
    if w or n or st.get("ahead"):
        return "2", "你这边有改动：建议推送"
    return "", "已经全部同步，没有要干的活"


# --------------------------------------------------------------------- 动作
def act_pull(st):
    print()
    hr("═")
    print(C_HEAD + "  ① 获取队友的更新（拉取 → 镜像到工作目录 → 自检）" + C_END)
    hr("═")
    rc, out = run_py("git_sync.py", ["pull"])
    if rc == 0 and "自检         通过" in out:
        print("\n" + C_OK + "  [OK] 搞定：队友的更新已经在你的工作目录里，自检 35/35。" + C_END)
    elif rc == 4:
        print("\n" + C_ERR + "  [!] 拉回来了但自检没过 —— 先别用！" + C_END)
        print("    回滚命令：python tools\\git_sync.py rollback")
    elif rc == 0:
        print("\n" + C_OK + "  [OK] 完成（没有需要更新的内容）。" + C_END)
    else:
        print("\n" + C_ERR + "  [!] 拉取过程中出问题了，看上面的输出。" + C_END)
    return rc


def act_push(st):
    print()
    hr("═")
    print(C_HEAD + "  ② 把我的改动推到仓库（送进包 → 判据 → 自检 → 提交推送）" + C_END)
    hr("═")
    # 1) 工作区 → 协作包
    print(C_HEAD + "\n[1/4] 把工作区改过的文件送进协作包" + C_END)
    rc, out = run_py("git_sync.py", ["out"])
    if rc != 0:
        print(C_ERR + "  送进包失败。" + C_END); return rc
    # 2) 打包判据（缺文件就别推）
    print(C_HEAD + "\n[2/4] 打包完整性判据" + C_END)
    rc, out = run_py("check_share_pack.py")
    report = os.path.join(st["work"], "reports", CHECK_REPORT)
    miss, lack, enc = [], [], 0
    if os.path.exists(report):
        txt = io.open(report, encoding="utf-8").read()
        m = re.search(r"## 意外漏掉的文件（必须补）\n\n((?:- `[^\n]+\n?)+)", txt)
        if m:
            miss = re.findall(r"- `([^`]+)`", m.group(1))
        m2 = re.search(r"## 关键文件缺失\n\n((?:- `[^\n]+\n?)+)", txt)
        if m2:
            lack = re.findall(r"- `([^`]+)`", m2.group(1))
        m3 = re.search(r"\| 脚本编码不对[^|]*\| (\d+) \|", txt)
        enc = int(m3.group(1)) if m3 else 0

    if miss:
        print(C_WARN + "\n  发现 %d 个文件在仓库里没有（可能是你新写的）：" % len(miss) + C_END)
        for x in miss[:15]:
            print("     " + x)
        if len(miss) > 15:
            print("     ... 还有 %d 个" % (len(miss) - 15))
        ans = ask("  要把它们也加进仓库一起提交吗？[y/N] ", default="n")
        if ans.lower().startswith("y"):
            added = 0
            for rel in miss:
                src = os.path.join(st["work"], rel.replace("/", os.sep))
                dst = os.path.join(st["pack"], rel.replace("/", os.sep))
                if os.path.exists(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    added += 1
                    print("     已加入: " + rel)
            print(C_OK + "  加进包 %d 个文件，重新判据……" % added + C_END)
            rc, out = run_py("check_share_pack.py")
            if os.path.exists(report):
                txt = io.open(report, encoding="utf-8").read()
                m = re.search(r"## 意外漏掉的文件（必须补）\n\n((?:- `[^\n]+\n?)+)", txt)
                miss = re.findall(r"- `([^`]+)`", m.group(1)) if m else []
    if lack:
        print(C_ERR + "  关键文件缺失 %d 个（源码/配置没在包里）：" % len(lack) + C_END)
        for x in lack[:10]:
            print("     " + x)
        print("  → 这几个必须先补进包再推，否则队友克隆下来会崩。本次已停止。")
        return 5
    if enc:
        print(C_ERR + "  有 %d 个脚本编码不对（.ps1 缺 BOM / .bat 非 GBK），先修再推：" % enc + C_END)
        print("      python tools\\fix_script_encoding.py --dir \"%s\"" % st["pack"])
        return 6
    if not miss:
        print(C_OK + "  判据通过：意外漏掉 0 / 关键文件缺失 0 / 脚本编码 0" + C_END)
    # 3) 自检
    print(C_HEAD + "\n[3/4] 自检（确认代码没坏）" + C_END)
    p = subprocess.run([sys.executable, "-m", "ai_modeler", "--selftest"],
                       cwd=st["work"], capture_output=True)
    txt = p.stdout.decode("utf-8", "replace")
    line = [l for l in txt.splitlines() if "通过" in l and "/" in l]
    print("  " + (line[-1].strip() if line else "（没解析到结果）"))
    if p.returncode != 0:
        print(C_ERR + "  自检没过，已停止推送。" + C_END); return 7
    # 4) 提交推送
    print(C_HEAD + "\n[4/4] 提交并推送" + C_END)
    n_files = st["ws_changed"] + st["ws_new"]
    default_msg = "chore: 同步本地改动（%d 个文件）" % n_files if n_files else "chore: 同步"
    msg = ask("  提交说明（直接回车用默认）: ", default=default_msg, allow_empty=True)
    rc, out = run_py("push_to_github.py", ["push", "--message", msg])
    if rc == 0:
        print("\n" + C_OK + "  [OK] 已推上 GitHub。队友下次 pull 就能拿到。" + C_END)
    else:
        print("\n" + C_ERR + "  [!] 推送失败，看上面输出。" + C_END)
    return rc


def act_both(st):
    rc = act_pull(st)
    if rc == 4:
        print(C_WARN + "  拉取后自检没过，先别推自己的改动（避免把坏状态推上去）。" + C_END)
        return rc
    return act_push(status())


def act_status(st):
    print()
    print("  " + C_DIM + "（状态就是上面那张表；这里再看一次实时结果）" + C_END)
    show_status(status())
    return 0


def act_rollback(st):
    print()
    hr("═")
    print(C_HEAD + "  ④ 回滚上一次拉取" + C_END)
    hr("═")
    ans = ask("  确定要回滚到上一次拉取之前吗？[y/N] ", default="n")
    if not ans.lower().startswith("y"):
        print("  已取消。")
        return 0
    rc, out = run_py("git_sync.py", ["rollback"])
    return rc


def act_run(st):
    print()
    hr("═")
    print(C_HEAD + "  ⑤ 启动 AI 自动建模助手（顺便确认能用）" + C_END)
    hr("═")
    exe = os.path.join(st["work"], "启动 AI自动建模助手.vbs")
    if os.path.exists(exe):
        os.startfile(exe)                       # noqa: S606
        print(C_OK + "  已启动（界面窗口会稍后弹出）。" + C_END)
    else:
        subprocess.Popen([sys.executable, "-m", "ai_modeler"], cwd=st["work"])
        print(C_OK + "  已启动 python -m ai_modeler。" + C_END)
    return 0


# --------------------------------------------------------------------- 输入
class UserExit(Exception):
    """没有输入了（管道/重定向/关掉窗口）—— 必须**退出**，绝不能当"回车=用默认值"。

    实测踩过的严重事故（写在这里防止再犯）：
        用管道喂了一个带 BOM 的 "0"，没被识别成退出码，于是进入下一轮；
        而 stdin 已经 EOF，input() 立刻抛 EOFError，
        ask() 当时"贴心地"返回了默认值 = 建议动作 = **推送**，
        结果菜单在无人值守的情况下**循环推送了 7 个提交**。
    规矩：**只有真人敲了回车才用默认值**；读不到输入就退出。
    """


def ask(prompt, default="", allow_empty=False):
    try:
        s = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise UserExit()
    if not s:
        if default:
            # 只有**真人在终端里敲回车**才允许用默认值(=执行建议动作)。
            # 输入来自管道/重定向时一律退出：绝不能让脚本"自己决定"去推送。
            if sys.stdin.isatty():
                return default
            raise UserExit()
        return ""
    return s


def wait_key(prompt="\n按回车回到菜单……"):
    try:
        input(C_DIM + prompt + C_END)
    except (EOFError, KeyboardInterrupt):
        print()
        raise UserExit()


def main():
    import console_io
    console_io.setup()
    enable_color()
    try:
        return _main_loop()
    except UserExit:
        print(C_DIM + "  没有更多输入了，已退出（未执行任何未确认的动作）。" + C_END)
        return 0


def _main_loop():
    while True:
        st = status()
        rec, why = show_status(st)
        if st.get("error"):
            wait_key()
            return 1
        print("  " + C_HEAD + "建议：%s" % why + C_END)
        print()
        print("   " + C_HEAD + "[1]" + C_END + " 获取队友的更新（拉取 → 落到工作目录 → 自检）")
        print("   " + C_HEAD + "[2]" + C_END + " 把我的改动推到仓库（判据 → 自检 → 提交推送）")
        print("   " + C_HEAD + "[3]" + C_END + " 两边都同步（先拉后推）")
        print("   " + C_HEAD + "[4]" + C_END + " 回滚上一次拉取")
        print("   " + C_HEAD + "[5]" + C_END + " 启动 AI 自动建模助手")
        print("   " + C_HEAD + "[0]" + C_END + " 退出")
        hint = " 直接回车 = 执行建议动作(%s)" % rec if rec else " 直接回车 = 刷新状态"
        choice = ask("\n  请选择（输入数字）" + C_DIM + hint + C_END + ": ", default=rec)
        if choice in ("0", "q", "Q", "exit"):
            print("  再见。")
            return 0
        if choice in ("", "r", "R"):
            if rec:
                choice = rec
            else:
                continue
        fn = {"1": act_pull, "2": act_push, "3": act_both,
              "4": act_rollback, "5": act_run}.get(choice)
        if not fn:
            print(C_WARN + "  看不懂 '%s'，请输入 1/2/3/4/5/0。" % choice + C_END)
            wait_key()
            continue
        try:
            fn(st)
        except KeyboardInterrupt:
            print(C_WARN + "\n  已中断。" + C_END)
        except Exception as e:
            print(C_ERR + "\n  出错：%s: %s" % (type(e).__name__, e) + C_END)
        if fn is act_run:
            return 0
        wait_key()

if __name__ == "__main__":
    sys.exit(main())
