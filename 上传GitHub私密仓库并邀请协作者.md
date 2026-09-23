# 怎么上传到 GitHub 私密仓库、并邀请协作者

> 面向"不想学 git 细节"的用法：**方式 A（网页 + GitHub Desktop）**最省心，
> **方式 B（命令行）**最标准。两种都给出逐步操作。
> 你的文件夹已经准备好了：`桌面\AI建模助手_协作包`（473 个文件，46 MB）。

---

## 0. 先明确三件事

1. **仓库要建成 Private（私密）**：别人搜不到、看不到；只有被邀请的人能访问。
2. **私密仓库邀请人**：对方必须用 GitHub 账号**接受邀请**，之后才能看到代码。
3. **大文件别硬传**：GitHub 单文件上限 **100 MB**，仓库超过 **1 GB** 会被警告/限制。
   我们已经把可重建的大件（513 MB 的轮子、120 MB 的实测网格）排除在外，
   并在 `README.md` 第三节写了"一条命令重建"的办法。

---

## 方式 A：网页 + GitHub Desktop（推荐，不用敲命令）

### A1. 装 GitHub Desktop
1. 打开 <https://desktop.github.com> 下载安装（Windows 版）；
2. 首次启动 → `Sign in to GitHub.com` → 用你的账号登录（浏览器会弹授权页，点 Authorize）。

### A2. 把文件夹变成仓库
1. GitHub Desktop 菜单：`File` → `Add local repository…`；
2. 选 `C:\Users\25643\Desktop\AI建模助手_协作包`；
3. 它会提示 *"This directory does not appear to be a Git repository. Would you like to create a repository here?"* → 点 **create a repository**；
4. 在弹窗里填：
   * **Name**：`ai-modeler`（仓库名，英文，别用中文）
   * **Description**：`AI 自动建模助手：提示词 → 真实零件模型（含判据与闭环）`
   * **Keep this code private** ✅ **务必勾上**（这就是私密仓库）
   * **Git ignore**：`None`（我们已经自带 `.gitignore`）
   * **License**：`MIT`（或按需）
5. 点 **Create repository** → 它就替你做完了 `git init`。

### A3. 提交并上传
1. 左侧会列出 473 个文件；左下角：
   * **Summary**：`chore: 初始提交（源码+文档+判据+跑图对比）`
   * **Description**（可选）：`几何相似度 99.8%（498/502）；图像相似度 84.5%`
2. 点 **Commit to main**；
3. 顶部点 **Publish repository**：
   * 取消勾选 `Keep this code private` 的**反选**（保持私密 ✅）
   * 点 **Publish repository** → 上传完成（46 MB，几分钟）。

### A4. 邀请协作者
1. 在 GitHub Desktop 里：`Repository` → `View on GitHub`（打开网页）；
2. 网页里：仓库 → **Settings**（顶部标签）→ 左栏 **Collaborators**；
3. 点 **Add people** → 输入对方的 **GitHub 用户名或邮箱** → 选人 → **Add … to this repository**；
4. 对方会收到邀请邮件/站内通知，**接受后**即可 `git clone`、提 PR。

> 权限说明：`Write` 就够协作了；要改仓库设置才需要 `Admin`。

---

## 方式 B：命令行（标准做法，装一次 Git 就够）

### B1. 装 Git
下载安装 <https://git-scm.com/download/win>（一路下一步即可），
装完新开一个 PowerShell 窗口，确认：

```powershell
git --version
```

### B2. 配置身份（只需一次）
```powershell
git config --global user.name  "你的名字"
git config --global user.email "你的邮箱@example.com"
```

### B3. 在 GitHub 网页新建私密仓库
1. 右上角 **+** → **New repository**；
2. **Repository name**：`ai-modeler`；
3. **Visibility**：**Private** ✅；
4. **不要**勾 `Add a README file`（本地已经有）；
5. **Create repository** → 记下页面给的地址，形如
   `https://github.com/你的用户名/ai-modeler.git`。

### B4. 本地初始化并推送
```powershell
cd C:\Users\25643\Desktop\AI建模助手_协作包

git init -b main
git add .
git commit -m "chore: 初始提交（源码+文档+判据+跑图对比）"
git remote add origin https://github.com/你的用户名/ai-modeler.git
git push -u origin main
```

第一次 `push` 会弹**浏览器登录/凭据窗口**：用 GitHub 账号授权即可
（或者用 Personal Access Token 当密码，见附一）。

### B5. 邀请协作者（命令行也行，但网页更直观）
网页：仓库 → **Settings** → **Collaborators** → **Add people** → 输入对方账号 → 邀请。

命令行（需要 GitHub CLI，可选）：
```powershell
gh auth login
gh api -X PUT repos/你的用户名/ai-modeler/collaborators/对方用户名 -f permission=push
```

---

## 附一：如果 push 一直要密码 / 报 403

GitHub 2021 年起**不能用账号密码**推代码，要用 **Personal Access Token (PAT)**：

1. 网页 → 右上角头像 → **Settings** → 左栏最下 **Developer settings**；
2. **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**；
3. 勾选 **repo** 权限，有效期按需（比如 90 天）→ 生成；
4. **复制那串 token**（只显示一次）；
5. push 时把 token 当成密码粘贴（用户名填你的 GitHub 用户名）。
   或保存到凭据管理器，凭据提示里选 "Store"。

---

## 附二：后面还要传"实时几何网格"这类大文件时

本包已经把大数据排除；如果将来确实要传进仓库，用 **Git LFS**：

```powershell
git lfs install
git lfs track "kb/real_brep/*.npz" "零件真实图片_带背景/**/*.png"
git add .gitattributes
git commit -m "chore: 用 LFS 管理大数据"
```

> 注意：GitHub 免费额度是 **1 GB 存储 + 1 GB/月流量**，超了要买流量包。
> 更省的做法：大数据放**网盘/对象存储**，仓库里只放"重建脚本 + 校验和"。

---

## 附三：协作者拿到之后怎么跑

```powershell
git clone https://github.com/你的用户名/ai-modeler.git
cd ai-modeler

python -m ai_modeler --selftest        # 先自检：应 35/35
python tools\bench_detail_fidelity.py  # 几何判据：应 ≈99.8%
```

要跑"图像相似度"和"真实照片对比"，需要先重建大数据（见 `README.md` 第三节），
其中真实照片必须用**装了 CATIA 的机器**拍。

---

## 附四：一句话清单（照着勾）

- [ ] 注册/登录 GitHub
- [ ] 新建**私密**仓库 `ai-modeler`
- [ ] 用 GitHub Desktop 添加本地文件夹 → Create repository（勾 Keep this code private）
- [ ] Commit → Publish（确认仍是 Private）
- [ ] Settings → Collaborators → Add people → 输入对方账号
- [ ] 对方接受邀请 → clone → 跑自检
