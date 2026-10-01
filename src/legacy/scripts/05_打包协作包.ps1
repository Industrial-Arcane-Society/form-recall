# -*- coding: utf-8 -*-
<#
    05_打包协作包.ps1 —— 一条命令完成"校验 + 打包"

    为什么要先校验再打包：
        队友实测踩过坑：.gitignore 里写了 catia_*.py，把正儿八经的源码
        ai_modeler/catia_export.py 一起忽略了，git **静默不报错**，
        仓库里那个文件根本不存在，安装脚本一跑就 ModuleNotFoundError。
        所以打包前必须跑 tools\check_share_pack.py，三项全 0 才算通过：
            意外漏掉 = 0、关键文件缺失 = 0、.gitignore 误伤 = 0

    用法（在协作包目录里双击或在项目目录里跑）：
        powershell -ExecutionPolicy Bypass -File scripts\05_打包协作包.ps1
#>
param(
    [string]$PackDir = "C:\Users\25643\Desktop\AI建模助手_协作包",
    [string]$ZipPath = "C:\Users\25643\Desktop\AI建模助手_协作包.zip"
)
$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 的控制台默认是 GBK，Python 打出来的 UTF-8 会变乱码，先切 UTF-8
try {
    chcp 65001 | Out-Null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = "utf-8"
} catch { }
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "=== 1/3 打包完整性校验 ===" -ForegroundColor Cyan
python tools\check_share_pack.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "校验不通过：上面标了 [漏] 的文件必须先补进协作包，或写进 NOT_PACKED 并说明理由。" -ForegroundColor Red
    Write-Host "报告：reports\协作包完整性检查.md" -ForegroundColor Yellow
    exit 1
}

Write-Host "`n=== 2/3 同步校验报告到协作包 ===" -ForegroundColor Cyan
Copy-Item "reports\协作包完整性检查.md" (Join-Path $PackDir "reports\协作包完整性检查.md") -Force

Write-Host "`n=== 3/3 压缩（交给 Python 的 zipfile，保证跨平台）===" -ForegroundColor Cyan
# 为什么不用 Compress-Archive：它在这台机器上把条目名写成反斜杠（ai_modeler\app.py），
# 队友在 macOS/Linux 解压会丢目录结构。tools\make_share_zip.py 用 zipfile 生成规范的正斜杠，
# 并自动排除 .git、打完回读校验关键文件。
python tools\make_share_zip.py --src $PackDir --out $ZipPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "打包校验没通过，看上面的 **问题** 行。" -ForegroundColor Red
    exit 1
}
Write-Host "`n接下来按 docs\更新协作包到私密仓库.md 把它推上私密仓库。" -ForegroundColor Yellow
