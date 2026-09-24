# -*- coding: utf-8 -*-
<#
    06_初始化Git仓库.ps1 —— 给"从 zip 解出来的协作包"补上 .git

    什么时候需要它：
        协作包是以 zip 形式发出去的，**zip 里不含 .git 目录**（否则几十 MB 的
        git 对象库会跟着包走，而且别人的历史不该混进来）。
        所以刚解压出来的包在 GitHub Desktop 眼里"不是仓库"，
        点「Add local repository」会报：
            C:\...\AI建模助手_协作包 isn't a Git repository.

        本机没装 git 也没关系：GitHub Desktop **自带** git，本脚本会自动找到它。

    做完之后（在 GitHub Desktop 里）：
        文件 → Add local repository → 选本目录 → 它会列出 600 个文件
        → 底部填提交信息 → Commit to main
        → 顶部 Publish repository → **勾上 Keep this code private** → 发布

    用法:
        powershell -ExecutionPolicy Bypass -File scripts\06_初始化Git仓库.ps1
        powershell -ExecutionPolicy Bypass -File scripts\06_初始化Git仓库.ps1 -PackDir "D:\某处\AI建模助手_协作包"
#>
param(
    [string]$PackDir = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = "Stop"
try {
    chcp 65001 | Out-Null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

function Find-Git {
    $cmd = Get-Command git -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $roots = @("$env:LOCALAPPDATA\GitHubDesktop", "$env:LOCALAPPDATA\Programs\GitHub Desktop",
               "${env:ProgramFiles}\GitHub Desktop", "${env:ProgramFiles}\Git")
    foreach ($r in $roots) {
        if (-not (Test-Path $r)) { continue }
        $hit = Get-ChildItem $r -Recurse -Filter git.exe -ErrorAction SilentlyContinue |
               Where-Object { $_.FullName -match '\\cmd\\git\.exe$' } |
               Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

if (-not (Test-Path $PackDir)) { Write-Host "目录不存在：$PackDir" -ForegroundColor Red; exit 1 }
$PackDir = (Resolve-Path $PackDir).Path

$git = Find-Git
if (-not $git) {
    Write-Host "找不到 git。两个办法：" -ForegroundColor Red
    Write-Host "  A) 装 GitHub Desktop（它自带 git）：https://desktop.github.com" -ForegroundColor Yellow
    Write-Host "  B) winget install --id Git.Git -e" -ForegroundColor Yellow
    exit 1
}
Write-Host "使用 git：$git" -ForegroundColor Cyan
& $git --version
Set-Location $PackDir

if (Test-Path (Join-Path $PackDir ".git")) {
    Write-Host "`n本目录已经是 git 仓库了，不用重复初始化。当前状态：" -ForegroundColor Green
    & $git status -sb | Select-Object -First 5
    exit 0
}

Write-Host "`n=== 1/3 初始化仓库（分支 main）===" -ForegroundColor Cyan
& $git init -b main

Write-Host "`n=== 2/3 本仓库级别的配置（不动你的全局配置）===" -ForegroundColor Cyan
# 中文文件名在 git status / log 里不要显示成 \350\207\252 这种转义
& $git config --local core.quotepath false
# Windows 上换行符处理：交给各人本地决定，避免"整文件都被改了"的假 diff
& $git config --local core.autocrlf false
& $git config --local core.longpaths true
Write-Host "  core.quotepath=false / core.autocrlf=false / core.longpaths=true"

Write-Host "`n=== 3/3 把所有文件加进暂存区（先不提交）===" -ForegroundColor Cyan
& $git add -A
$staged = (& $git diff --cached --name-only | Measure-Object).Count
Write-Host "  已暂存 $staged 个文件"

Write-Host "`n=== 自检：出事的那个文件有没有被忽略（无输出 = 通过）===" -ForegroundColor Cyan
& $git check-ignore -v ai_modeler/catia_export.py
Write-Host "（上面没有输出就说明没被忽略）"
$catias = (& $git diff --cached --name-only | Select-String -Pattern 'catia_export|catialib' | Measure-Object).Count
Write-Host "  暂存区里的 catia 源码文件数：$catias（应 ≥ 2）"

Write-Host "`n完成。接下来在 GitHub Desktop 里：" -ForegroundColor Green
Write-Host "  1) 文件 → Add local repository → 选：$PackDir" -ForegroundColor Yellow
Write-Host "  2) 底部填提交信息 → Commit to main" -ForegroundColor Yellow
Write-Host "  3) 顶部 Publish repository → 勾上 Keep this code private → 发布" -ForegroundColor Yellow
Write-Host "`n（首次提交建议用你自己的 GitHub 账号提交，所以这里**不替你提交**，" -ForegroundColor Gray
Write-Host "  这样 commit 作者是你，不是脚本。）" -ForegroundColor Gray
