# -*- coding: utf-8 -*-
<#
    07_拉取队友更新.ps1 —— 把队友在 GitHub 上的更新，拉到本地并落到工作目录

    为什么需要这个脚本（而不是直接 git pull）：
        仓库（GitHub 私有库）里**只有代码**，大数据故意不进仓库
        （kb/real_brep* 约 230 MB、_wheels 513 MB、现场照片、零件参数…）。
        而大数据在**工作目录** C:\Users\25643\Desktop\ai自动建模插件 里，
        那个目录又不是 git 仓库。所以"用队友的更新"要两步：
            ① 在协作包（= git 工作副本）里 git pull；
            ② 把这次变更的文件镜像到工作目录的对应位置。
        本脚本 = 这两步 + 门禁自检 + 可回滚，一条命令搞定。

    用法（双击本文件也行，或在项目目录里跑）:
        powershell -ExecutionPolicy Bypass -File scripts\07_拉取队友更新.ps1
        powershell -ExecutionPolicy Bypass -File scripts\07_拉取队友更新.ps1 check
        powershell -ExecutionPolicy Bypass -File scripts\07_拉取队友更新.ps1 full
        powershell -ExecutionPolicy Bypass -File scripts\07_拉取队友更新.ps1 rollback
#>
param(
    [string]$Mode = "pull"
)
$ErrorActionPreference = "Continue"
try {
    chcp 65001 | Out-Null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = "utf-8"
} catch { }

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = "python"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    $cand = "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe"
    if (Test-Path $cand) { $py = $cand } else {
        Write-Host "找不到 python，请先装 Python 3.9+ 并勾选 Add Python to PATH" -ForegroundColor Red
        Read-Host "按回车退出"; exit 1
    }
}

switch ($Mode) {
    "check"    { & $py tools\git_sync.py check }
    "pull"     { & $py tools\git_sync.py pull }
    "full"     { & $py tools\git_sync.py pull --gates full }
    "rollback" { & $py tools\git_sync.py rollback }
    "nomirror" { & $py tools\git_sync.py pull --no-mirror }
    default {
        Write-Host "用法: 07_拉取队友更新.ps1 [check|pull|full|rollback|nomirror]" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "（上面若显示 '自检 通过 35 / 35' 就说明这次更新是好的，可以直接用。）" -ForegroundColor Green
Write-Host "（若自检没过：python tools\git_sync.py rollback 可以退回更新前。）" -ForegroundColor Yellow
Read-Host "按回车关闭窗口"
