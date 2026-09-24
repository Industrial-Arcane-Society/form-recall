# -*- coding: utf-8 -*-
r"""把队友在 GitHub 上的更新，拉到本地并**落到你真正在用的工作目录**里。

背景（为什么不能只 `git pull` 就完事）：
    仓库里只有**代码**，大数据故意不进仓库（`kb/real_brep*` 约 230 MB、`_wheels` 513 MB、
    现场照片、`零件参数` 等，理由见 README 第三节）。
    而**大数据在你的工作目录** `C:\Users\25643\Desktop\ai自动建模插件` 里 ——
    但那个目录不是 git 仓库。所以"拉更新"要分两步：
        1) 在**协作包**（= git 工作副本，与仓库结构完全一致）里 `git pull`；
        2) 把这次变更的**文件**镜像到你工作目录的对应位置（保留你原来的目录布局）。
    本脚本把这两步 + 门禁验证 + 可回滚，做成一条命令。

为什么镜像要按 git 的 diff 来做（而不是整个目录覆盖）：
    * 只碰队友真正改动过的文件，其余一律不动；
    * 队友删掉的文件，这边也删（但要先比对哈希，确认本地那份没被你改过，改过就不删）；
    * 覆盖前把旧文件备份到 `_mirror_backup\<时间戳>\`，随时能拿回来。

用法（在工作区目录里跑）:
    python tools\git_sync.py check                 # 只读：看远端有什么新提交、会动哪些文件
    python tools\git_sync.py pull                  # 拉取 + 镜像到工作区 + 跑自检
    python tools\git_sync.py pull --no-mirror      # 只更新协作包，不动工作区
    python tools\git_sync.py pull --gates full     # 拉完顺带跑几何判据（慢一点）
    python tools\git_sync.py pull --dry-run        # 只说会做什么，不落盘
    python tools\git_sync.py rollback              # 回到上一次 pull 之前（含工作区文件还原）
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from push_to_github import DEF_OWNER, DEF_REPO, find_git, github_token, run   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_PACK = r"C:\Users\25643\Desktop\AI建模助手_协作包"
DEF_WORK = r"C:\Users\25643\Desktop\ai自动建模插件"
STATE_NAME = "git_sync_state.json"


def default_work():
    """判断"我"是谁：脚本装在协作包里就说明工作区是另一个目录，反之亦然。

    这样同一份工具在两个目录里都能正确工作（双击哪个都行）。
    """
    here = os.path.abspath(ROOT)
    pack = os.path.abspath(DEF_PACK)
    if os.path.normcase(here) == os.path.normcase(pack):
        return DEF_WORK
    return here

#: 仓库里被"重新归档"过的目录 → 在工作区里其实在根目录（保持你原来的布局，不另建目录）
ARCHIVE_MAP = [("docs/历史报告/", ""), ("启动脚本/", "")]

#: 这些在仓库里是数据/产物，不要镜像回工作区（都是大数据，本来也不会出现在 diff 里）
NEVER_MIRROR = ("kb/real_brep", "kb/real_brep_coarse", "_wheels", "零件真实图片",
                "零件真实图片_带背景", "零件AI建模图片", "零件比对图", "零件参数", ".git/")


def g(git, args, cwd):
    rc, out, err = run(git, args, cwd, quiet=True)
    return rc, out.strip(), err.strip()


def state_path(pack):
    return os.path.join(pack, ".git", STATE_NAME)


def load_state(pack):
    p = state_path(pack)
    if os.path.exists(p):
        try:
            return json.load(io.open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(pack, d):
    with io.open(state_path(pack), "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2))


def ws_target(rel, work):
    """仓库相对路径 → 工作区里的目标路径（处理"重新归档"过的那两类）。"""
    for pref, _ in ARCHIVE_MAP:
        if rel.startswith(pref):
            direct = os.path.join(work, os.path.basename(rel))
            if os.path.exists(direct):
                return direct        # 工作区里本来就在根目录 → 还放根目录
    return os.path.join(work, rel.replace("/", os.sep))


def blob_sha(git, rev, rel, cwd):
    rc, out, _ = g(git, ["rev-parse", "%s:%s" % (rev, rel)], cwd)
    return out if rc == 0 else ""


def file_sha(git, path, rel_hint, cwd):
    """算工作区文件"按仓库规则归一化后"的哈希，用来判断内容是否真的不一样。"""
    rc = subprocess.run([git, "hash-object", "--path=" + rel_hint, path],
                        cwd=cwd, capture_output=True)
    return rc.stdout.decode().strip() if rc.returncode == 0 else ""


def changed(git, old, new, cwd):
    if not old or not new or old == new:
        return []
    rc, out, _ = g(git, ["diff", "--name-status", "-z", old, new], cwd)
    if rc != 0:
        return []
    parts = [p for p in out.split("\x00") if p]
    res, i = [], 0
    while i < len(parts):
        st = parts[i]
        if st[:1] in ("R", "C"):            # 重命名/复制：st, old, new
            res.append((st[:1], parts[i + 2]))
            i += 3
        else:
            res.append((st[:1], parts[i + 1]))
            i += 2
    return res


def mirror(git, old, new, pack, work, backup, dry):
    """把 old→new 之间的变更镜像到工作区。返回 (动作列表, 时间戳, 被覆盖/删除的文件集合)。"""
    acts = []
    backed = set()
    ts = time.strftime("%Y%m%d_%H%M%S")
    for st, rel in changed(git, old, new, pack):
        if any(rel == n or rel.startswith(n) for n in NEVER_MIRROR):
            acts.append(("跳过(大数据)", rel)); continue
        dst = ws_target(rel, work)
        src = os.path.join(pack, rel.replace("/", os.sep))
        if st == "D":
            if not os.path.exists(dst):
                acts.append(("已不存在", rel)); continue
            oldsha = blob_sha(git, old, rel, pack)
            nowsha = file_sha(git, dst, rel, pack)
            if oldsha and nowsha and oldsha != nowsha:
                acts.append(("保留(你本地改过)", rel)); continue
            if not dry:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(dst, os.path.join(backup, rel.replace("/", os.sep)))
            acts.append(("删除", rel)); continue
        if not os.path.exists(src):
            acts.append(("源缺失", rel)); continue
        existed = os.path.exists(dst)
        # 内容比较用 git 自己的哈希（会按 .gitattributes 归一化换行），
        # 否则"只是 CRLF/LF 不同"也会被当成改动，白白重写一遍、还多出一份备份。
        src_sha = blob_sha(git, new, rel, pack)
        dst_sha = file_sha(git, dst, rel, pack) if existed else ""
        same = existed and src_sha and src_sha == dst_sha
        if same:
            acts.append(("已一致", rel)); continue
        if not dry:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if existed:
                b = os.path.join(backup, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(b), exist_ok=True)
                shutil.copy2(dst, b)
                backed.add(rel)
            shutil.copy2(src, dst)
        acts.append(("更新" if existed else "新增", rel))
    return acts, ts, backed


def scan_out(git, pack, work):
    """工作区相对仓库的改动：返回 (内容不同的已跟踪文件, 工作区里缺的, 工作区里多出来的新文件)。

    用途：把"我在工作目录改的东西"送进协作包，然后才能提交推上去。
    """
    rc, out, _ = g(git, ["ls-files"], pack)
    tracked = [l for l in out.splitlines() if l.strip()]
    differing, missing = [], []
    for rel in tracked:
        ws = ws_target(rel, work)
        if not os.path.exists(ws):
            missing.append(rel)
            continue
        a = blob_sha(git, "HEAD", rel, pack)
        b = file_sha(git, ws, rel, pack)
        if a and b and a != b:
            differing.append(rel)
    # 工作区里多出来的文件（仓库没有的）——排掉明显的数据/临时产物，剩下的交给
    # check_share_pack.py 用统一口径判断"该不该进包"
    tracked_ws = {os.path.normcase(ws_target(r, work)) for r in tracked}
    new = []
    for dp, dns, fns in os.walk(work):
        dns[:] = [d for d in dns
                  if d not in ("__pycache__", ".git", "_mirror_backup", "_selftest_out",
                               ".pytest_cache", "share_pack_files", "_wheels")]
        for fn in fns:
            p = os.path.join(dp, fn)
            rel_ws = os.path.relpath(p, work).replace("\\", "/")
            if any(rel_ws == n or rel_ws.startswith(n) for n in NEVER_MIRROR):
                continue
            if fn.startswith("_") or os.path.splitext(fn)[1].lower() in (".pyc", ".pyo", ".log", ".tmp"):
                continue
            if os.path.normcase(p) in tracked_ws:
                continue
            new.append(rel_ws)
    return sorted(differing), sorted(missing), sorted(new)


def do_out(git, pack, work, dry):
    """把工作区改过的文件（已跟踪的）镜像进协作包。返回动作列表。"""
    differing, missing, new = scan_out(git, pack, work)
    acts = []
    for rel in differing:
        src = ws_target(rel, work)
        dst = os.path.join(pack, rel.replace("/", os.sep))
        if not dry:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        acts.append(("送进包", rel))
    return acts, missing, new


def gates(work, mode):
    """跑门禁。返回 {名称: 结果文本}，并判断是否通过。"""
    out = {}
    def py(args, timeout=1800):
        p = subprocess.run([sys.executable] + args, cwd=work, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace"))
    rc, txt = py(["-m", "ai_modeler", "--selftest"])
    ok = rc == 0
    line = [l for l in txt.splitlines() if "通过" in l and "/" in l]
    out["自检"] = (line[-1].strip() if line else ("退出码 %d" % rc))
    out["自检_OK"] = ok
    if mode == "full":
        rc, txt = py(["tools\\bench_detail_fidelity.py"])
        pct = ""
        for l in txt.splitlines():
            if "几何相似度" in l or "相似度" in l:
                pct = l.strip()
        out["细节复现度"] = pct or ("退出码 %d" % rc)
        out["细节复现度_OK"] = rc == 0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["check", "pull", "out", "rollback"])
    ap.add_argument("--pack", default=DEF_PACK)
    ap.add_argument("--work", default=None)
    ap.add_argument("--ref", default="origin/main", help="要拉的分支（默认 origin/main）")
    ap.add_argument("--no-mirror", action="store_true", help="只更新协作包，不动工作区")
    ap.add_argument("--gates", choices=["fast", "full", "none"], default="fast")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--owner", default=DEF_OWNER)
    ap.add_argument("--repo", default=DEF_REPO)
    a = ap.parse_args()
    pack = os.path.abspath(a.pack)
    work = os.path.abspath(a.work) if a.work else default_work()
    git = find_git()
    if not git:
        print("找不到 git"); return 1
    tok, user = github_token()
    is_local_ref = not a.ref.startswith("origin/")

    # ---- 把工作区的改动送进协作包（推之前必须先做这一步）----
    if a.mode == "out":
        print("工作区 %s  →  协作包 %s" % (work, pack))
        acts, missing, new = do_out(git, pack, work, a.dry_run)
        print("\n=== 送进协作包：%d 个文件 ===" % len(acts))
        for k, rel in acts:
            print("     [%s] %s" % (k, rel))
        if not acts:
            print("     （工作区与仓库内容一致，没有要送的改动）")
        if missing:
            print("\n注意：仓库里有、工作区里没有的（不会自动删，自己确认）：")
            for m in missing[:20]:
                print("     " + m)
        if new:
            print("\n工作区里多出来的新文件 %d 个（要不要进仓库，由 sync_menu / check_share_pack 判断）：" % len(new))
            for n in new[:20]:
                print("     " + n)
        return 0

    if a.mode == "rollback":
        st = load_state(pack)
        if not st.get("before"):
            print("没有找到上一次 pull 的记录，没法自动回滚。")
            return 1
        print("回滚协作包到 %s ..." % st["before"][:8])
        if not a.dry_run:
            g(git, ["reset", "--hard", st["before"]], pack)
        bk = st.get("backup")
        n = 0
        if bk and os.path.isdir(bk):
            for dp, _, fns in os.walk(bk):
                for fn in fns:
                    src = os.path.join(dp, fn)
                    rel = os.path.relpath(src, bk)
                    dst = os.path.join(work, rel)
                    if not a.dry_run:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                    n += 1
        print("  工作区已还原 %d 个文件（来自 %s）" % (n, bk))
        print("  注意：队友**新增**的文件不会被删掉，需要的话手工删（脚本不猜你想删什么）。")
        return 0

    # ---- fetch ----
    print("仓库 %s/%s   协作包 %s" % (a.owner, a.repo, pack))
    if not is_local_ref:
        rc, out, err = run(git, ["fetch", "origin", "main"], pack, token=tok, owner=a.owner, quiet=True)
        if rc != 0:
            print("  fetch 失败：%s" % err[:300]); return 2
        print("  已从 GitHub 取到最新（用内存里的令牌，没有输密码）")
    rc, before, _ = g(git, ["rev-parse", "HEAD"], pack)
    rc, remote, _ = g(git, ["rev-parse", a.ref], pack)
    if rc != 0:
        print("  解析 %s 失败" % a.ref); return 2
    if before == remote:
        print("\n已经是最新（本地 HEAD = %s），队友还没有新东西。" % before[:8])
        return 0

    rc, log, _ = g(git, ["log", "--oneline", "%s..%s" % (before, remote)], pack)
    print("\n=== 远端有 %d 个新提交 ===" % len([l for l in log.splitlines() if l.strip()]))
    for l in log.splitlines():
        print("   " + l)
    ch = changed(git, before, remote, pack)
    print("\n=== 会改动 %d 个文件 ===" % len(ch))
    dirs = {}
    for st, rel in ch:
        key = rel.split("/")[0] if "/" in rel else "(根目录文件)"
        dirs.setdefault(key, []).append(st)
    for k in sorted(dirs):
        c = dirs[k]
        print("   %-16s %d 个（A新增 %d / M修改 %d / D删除 %d）"
              % (k, len(c), c.count("A"), c.count("M"), c.count("D")))

    if a.mode == "check":
        print("\n（check 模式：什么都没改。要真拉取跑：python tools\\git_sync.py pull）")
        return 0

    # ---- 先把工作区镜像做完，再动协作包的 HEAD？不行：镜像要读 pack 里的新文件。
    #      所以顺序是：先 merge（把新文件落到 pack），再镜像，再写状态。
    backup = os.path.join(work, "_mirror_backup", time.strftime("%Y%m%d_%H%M%S"))
    if not a.dry_run:
        os.makedirs(backup, exist_ok=True)
    if is_local_ref:
        # 本地分支测试用：只更新工作树，不改 HEAD
        if not a.dry_run:
            g(git, ["checkout", a.ref, "--", "."], pack)
        print("\n（--ref 指向本地分支：只更新协作包文件，不移动 HEAD）")
    else:
        rc, out, err = g(git, ["merge", "--ff-only", a.ref], pack)
        if rc != 0:
            print("\n** 快进合并失败 **：本地协作包可能有未提交改动，或历史已分叉。")
            print("   " + (err or out)[:400].replace("\n", "\n   "))
            print("   建议：cd 到协作包 → git status 看清楚 → 手工处理；或 git merge " + a.ref)
            return 3
        print("\n协作包已更新：%s → %s" % (before[:8], remote[:8]))

    if a.no_mirror:
        print("（--no-mirror：工作区未改动）")
    else:
        acts, ts, backed = mirror(git, before, remote, pack, work, backup, a.dry_run)
        cnt = {}
        for k, _ in acts:
            cnt[k] = cnt.get(k, 0) + 1
        print("\n=== 镜像到工作区 %s ===" % work)
        for k in sorted(cnt):
            print("   %-16s %d 个" % (k, cnt[k]))
        for k, rel in acts:
            if k in ("更新", "新增", "删除", "保留(你本地改过)", "源缺失"):
                print("     [%s] %s" % (k, rel))
        if not a.dry_run and backed:
            print("   被覆盖/删除的旧文件已备份到：%s" % backup)
        elif not a.dry_run:
            print("   （没有覆盖任何已有文件，所以没有产生备份）")

    if not a.dry_run:
        save_state(pack, {"before": before, "after": remote, "backup": backup,
                          "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                          "files": [r for k, r in acts if k in ("更新", "新增", "删除")]})

    if a.gates != "none":
        print("\n=== 门禁（在 %s 里跑）===" % work)
        res = gates(work, a.gates)
        for k in ("自检", "细节复现度"):
            if k in res:
                print("   %-10s %s" % (k, res[k]))
        if not res.get("自检_OK"):
            print("\n** 自检没过！** 先别用这套代码。回滚：python tools\\git_sync.py rollback")
            return 4
        print("   自检通过。")
    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
