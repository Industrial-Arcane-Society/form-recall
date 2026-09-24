# 02 跑一遍完整闭环（重建数据 → 出掩码 → 比对 → 出报告）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\02_跑全流程.ps1
#   可选参数：-StepDir "C:\path\to\step"   指定 CATIA 导出的 STEP 目录
param(
    [string]$StepDir = "$env:TEMP\catia_step_out"
)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
$env:AI_MODELER_BREP_COARSE = "1"

Write-Host "== 1/5 从 STEP 建实测 B-Rep 库（没有 STEP 就跳过）==" -ForegroundColor Cyan
if (Test-Path $StepDir) {
    python tools\build_real_brep.py --src $StepDir --tol 0.3
    python tools\build_real_brep.py --src $StepDir --tol 1.5 --out "kb\real_brep_coarse" --index "kb\real_brep_coarse_index.json"
} else {
    Write-Host "  找不到 $StepDir —— 先在 CATIA 里跑『导出真实几何_双击运行.vbs』" -ForegroundColor Yellow
}

Write-Host "== 2/5 渲染 AI 侧轮廓掩码（447 件约 10 分钟）==" -ForegroundColor Cyan
python tools\render_ai_masks.py --all

Write-Host "== 3/5 与真实照片比对（含视向重拟合）==" -ForegroundColor Cyan
python tools\compare_ai_vs_photos.py --all --montage 40

Write-Host "== 4/5 几何判据（硬指标）==" -ForegroundColor Cyan
python tools\bench_detail_fidelity.py
python tools\bench_real_geometry.py

Write-Host "== 5/5 出流程图与清单 ==" -ForegroundColor Cyan
python tools\make_loop_diagram.py

Write-Host "`n完成。看这几处：" -ForegroundColor Green
Write-Host "  reports\外轮廓闭环_流程图.png"
Write-Host "  reports\AI建模图_vs_真实照片_相似度.md"
Write-Host "  reports\细节复现度.md"
Write-Host "  零件比对图\（并排对比）"
