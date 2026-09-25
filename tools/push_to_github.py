# -*- coding: utf-8 -*-
r"""把协作包推上 GitHub（用 GitHub Desktop 已登录的令牌，不需要手输密码）。

为什么需要它：
    这台机器**没装 git**（用的是 GitHub Desktop 自带的 git 2.53.0），
    而且 GitHub 从 2021 年起不接受账号密码推送。但 GitHub Desktop 登录过之后，
    OAuth 令牌就存在 **Windows 凭据管理器**里（条目名 `GitHub - https://api.github.com/<用户名>`）。
    本脚本直接用它，省掉"生成 PAT → 存 → 手输"这一整套。

安全约定（重要）：
  * 令牌只读进内存，**不落盘、不写进命令行参数、不打印**（进程列表与日志里都看不到）；
  * 通过环境变量 + 一次性 credential helper 传给 git：
        git -c credential.helper= -c 'credential.helper=!f() { echo username=<user>; echo password=$GH_TOKEN; }; f'
  * 输出只打印 commit / 分支 / 文件数，不打印任何凭据。

用法:
    python tools\push_to_github.py status              # 看本地与远端差多少
    python tools\push_to_github.py push                # 提交并推送（自动接上远端历史）
    python tools\push_to_github.py verify              # 从 GitHub 侧核对结果
    python tools\push_to_github.py push --repo 别的仓库名 --owner 别的用户名
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as w
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_OWNER = "Mary111222"
DEF_REPO = "AI-_-"
DEF_PACK = r"C:\Users\25643\Desktop\AI建模助手_协作包"
CRED_TYPE_GENERIC = 1


# ---------------------------------------------------------------- 凭据管理器
class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", w.DWORD), ("dwHighDateTime", w.DWORD)]


class CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", w.DWORD), ("Type", w.DWORD), ("TargetName", w.LPWSTR),
                ("Comment", w.LPWSTR), ("LastWritten", FILETIME),
                ("CredentialBlobSize", w.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                ("Persist", w.DWORD), ("AttributeCount", w.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", w.LPWSTR),
                ("UserName", w.LPWSTR)]


def list_cred_targets():
    advapi = ctypes.WinDLL("Advapi32.dll")
    count = w.DWORD(0)
    pcreds = ctypes.POINTER(ctypes.POINTER(CREDENTIAL))()
    if not advapi.CredEnumerateW(None, 0, ctypes.byref(count), ctypes.byref(pcreds)):
        return []
    out = []
    for i in range(count.value):
        c = pcreds[i].contents
        out.append((c.TargetName or "", c.UserName or ""))
    advapi.CredFree(pcreds)
    return out


def read_cred(target):
    advapi = ctypes.WinDLL("Advapi32.dll")
    pcred = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        return None
    cred = pcred.contents
    raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    advapi.CredFree(pcred)
    for enc in ("utf-8", "utf-16-le"):
        try:
            s = raw.decode(enc).strip("\x00").strip()
            if s:
                return s
        except UnicodeDecodeError:
            continue
    return None


def github_token():
    """取 GitHub Desktop 登录时存下的 OAuth 令牌（只读，不打印）。"""
    for t, u in list_cred_targets():
        if t.startswith("GitHub - "):
            tok = read_cred(t)
            if tok:
                return tok, u
    return None, None


# ---------------------------------------------------------------------- HTTP
def api(url, token, method="GET", payload=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "ai-modeler-push")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, "%s: %s" % (type(e).__name__, e)


# ------------------------------------------------------------------------ git
def find_git():
    cands = [os.path.join(os.environ.get("LOCALAPPDATA", ""),
                          r"GitHubDesktop\app-3.6.6\resources\app\git\cmd\git.exe")]
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "GitHubDesktop")
    if os.path.isdir(base):
        for d in sorted(os.listdir(base), reverse=True):
            p = os.path.join(base, d, r"resources\app\git\cmd\git.exe")
            if os.path.exists(p):
                cands.append(p)
    for c in cands:
        if c and os.path.exists(c):
            return c
    from shutil import which
    return which("git")


def run(git, args, cwd, token=None, owner=DEF_OWNER, quiet=False):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        env["GH_TOKEN"] = token          # 只在这里出现，不进命令行
        args = ["-c", "credential.helper=",
                "-c", "credential.helper=!f() { echo username=%s; echo password=$GH_TOKEN; }; f" % owner] + args
    p = subprocess.run([git] + args, cwd=cwd, env=env, capture_output=True)
    out, err = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    if not quiet and err.strip():
        print("   [git] " + err.strip()[:400])
    return p.returncode, out, err


def head_sha(git, cwd, ref="HEAD"):
    rc, out, _ = run(git, ["rev-parse", "--short", ref], cwd, quiet=True)
    return out.strip() if rc == 0 else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["status", "push", "verify"])
    ap.add_argument("--owner", default=DEF_OWNER)
    ap.add_argument("--repo", default=DEF_REPO)
    ap.add_argument("--pack", default=DEF_PACK)
    ap.add_argument("--message", default=None)
    a = ap.parse_args()
    pack = os.path.abspath(a.pack)
    git = find_git()
    if not git:
        print("找不到 git（装 GitHub Desktop 或 Git for Windows 即可）")
        return 1
    tok, user = github_token()
    if not tok:
        print("取不到 GitHub 令牌：GitHub Desktop 没登录？还是凭据被系统策略挡住？")
        return 1
    url = "https://github.com/%s/%s.git" % (a.owner, a.repo)
    print("账号 %s → 仓库 %s" % (user, url))

    if a.mode in ("status", "push"):
        rc, out, _ = run(git, ["remote", "get-url", "origin"], pack, quiet=True)
        if rc != 0:
            run(git, ["remote", "add", "origin", url], pack, quiet=True)
        rc, out, err = run(git, ["fetch", "--depth", "1", "origin", "main"], pack, token=tok, quiet=True)
        remote_ok = rc == 0
        if a.mode == "status":
            rc, out, _ = run(git, ["diff", "--name-status", "origin/main"], pack, quiet=True) if remote_ok else (0, "", "")
            lines = [l for l in out.splitlines() if l.strip()]
            dels = [l for l in lines if l.startswith("D")]
            print("  远端取到: %s" % ("是" if remote_ok else "否（可能是空仓库）"))
            print("  改动条目: %d（其中远端会删除 %d 个）" % (len(lines), len(dels)))
            for l in dels[:40]:
                print("     " + l)
            print("  本地 HEAD: %s   远端 main: %s" % (head_sha(git, pack), head_sha(git, pack, "origin/main")))
            return 0

        # ---- push ----
        if remote_ok:
            run(git, ["reset", "--soft", "origin/main"], pack, quiet=True)
            print("  已接上远端历史（保留原有提交）")
        run(git, ["add", "-A"], pack, quiet=True)
        rc, out, _ = run(git, ["diff", "--cached", "--name-only"], pack, quiet=True)
        n = len([l for l in out.splitlines() if l.strip()])
        if n == 0:
            print("  没有需要提交的改动，直接看远端状态。")
        else:
            msg = a.message or "chore: 更新协作包（%d 个文件）" % n
            p = subprocess.run([git, "commit", "-F", "-"], cwd=pack,
                               input=msg.encode("utf-8"), capture_output=True)
            line = p.stdout.decode("utf-8", "replace").strip().splitlines()
            print("  提交 %d 个路径 → %s" % (n, line[0] if line else "?"))
            if p.returncode != 0:
                print("  提交失败：" + p.stderr.decode("utf-8", "replace")[:300])
                return 3
        rc, out, err = run(git, ["push", "origin", "main"], pack, token=tok)
        print("  push 退出码 %d" % rc)
        print("  " + (err.strip().splitlines()[-1] if err.strip() else out.strip()))
        return 0 if rc == 0 else 4

    # ---- verify ----
    st, repo = api("https://api.github.com/repos/%s/%s" % (a.owner, a.repo), tok)
    if st != 200:
        print("  查仓库失败 HTTP %s：%s" % (st, repo))
        return 1
    print("  私有=%s  体积=%s KB  默认分支=%s" % (repo["private"], repo["size"], repo["default_branch"]))
    st, commits = api("https://api.github.com/repos/%s/%s/commits?per_page=5" % (a.owner, a.repo), tok)
    for c in commits or []:
        print("    %s  %s  %s" % (c["sha"][:8], c["commit"]["author"]["date"],
                                  c["commit"]["message"].splitlines()[0][:66]))
    st, tree = api("https://api.github.com/repos/%s/%s/git/trees/%s?recursive=1"
                   % (a.owner, a.repo, repo["default_branch"]), tok)
    if st == 200:
        files = [t for t in tree["tree"] if t["type"] == "blob"]
        print("  远端文件数：%d" % len(files))
        paths = {t["path"] for t in files}
        must = ["ai_modeler/catia_export.py", "kb/README_数据说明.md", "kb/auto_parts.json",
                "docs/collaboration/更新协作包到私密仓库.md", "scripts/06_初始化Git仓库.ps1",
                ".gitattributes", ".github/workflows/acceptance.yml"]
        lack = [m for m in must if m not in paths]
        print("  关键文件：%s" % ("全部存在" if not lack else "**缺 %s**" % lack))
    return 0


if __name__ == "__main__":
    sys.exit(main())
