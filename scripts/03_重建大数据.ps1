# 03 重建"没进仓库的大数据"（按需执行）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\03_重建大数据.ps1
#   -What 可选：ocp / brep / photos / renders / all（默认 all）
param([string]$What = "all")
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"

function Do-Ocp {
    Write-Host "== OpenCASCADE 轮子（约 48+83 MB）==" -ForegroundColor Cyan
    python tools\fetch_ocp_wheel.py
    python -m pip install --no-input --no-index --no-deps --find-links "_wheels" cadquery-ocp-proxy cadquery-ocp
    $vtk = Get-ChildItem "_wheels" -Filter "vtk-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($vtk) { python -m pip install --no-input --no-index --no-deps $vtk.FullName }
}
function Do-Brep {
    Write-Host "== 实测 B-Rep 库（需要 STEP）==" -ForegroundColor Cyan
    $d = "$env:TEMP\catia_step_out"
    if (Test-Path $d) { python tools\build_real_brep.py --src $d --tol 0.3 } else {
        Write-Host "  没有 $d：先在 CATIA 里双击『导出真实几何_双击运行.vbs』" -ForegroundColor Yellow
    }
}
function Do-Photos {
    Write-Host "== 真实照片（需要 CATIA 会活）==" -ForegroundColor Cyan
    Write-Host "  1) 整窗截图（旧口径）：python tools\capture_real_images.py"
    Write-Host "  2) 双帧（对比口径，推荐）：python tools\reshoot_real_parts.py"
}
function Do-Renders {
    Write-Host "== AI 渲染图 + 掩码 ==" -ForegroundColor Cyan
    python tools\render_ai_masks.py --all
    python tools\render_ai_parts.py --all
}

switch ($What) {
    "ocp"     { Do-Ocp }
    "brep"    { Do-Brep }
    "photos"  { Do-Photos }
    "renders" { Do-Renders }
    default   { Do-Ocp; Do-Brep; Do-Photos; Do-Renders }
}
Write-Host "`n完成。" -ForegroundColor Green
