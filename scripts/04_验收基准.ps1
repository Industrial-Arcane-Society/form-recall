# 04 验收基准（改代码前后都跑这个，任何一项变红就回滚）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\04_验收基准.ps1
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"

$fail = 0
function Step($name, $cmd) {
    Write-Host "`n== $name ==" -ForegroundColor Cyan
    & $cmd
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [不通过] $name（退出码 $LASTEXITCODE）" -ForegroundColor Red
        $script:fail++
    } else {
        Write-Host "  [通过] $name" -ForegroundColor Green
    }
}

Step "自检（应 35/35）"            { python -m ai_modeler --selftest }
Step "几何相似度（对实测 STEP）"    { python tools\bench_detail_fidelity.py }
Step "真实几何命中"                { python tools\bench_real_geometry.py }
Step "CATIA 通道"                  { python tools\check_catia_export_real.py }
Step "CATIA 孔位几何"              { python tools\check_catia_hole_positions.py --all }
Step "自研布尔等价性"              { python tools\check_bsp_local.py }

Write-Host "`n==============================" -ForegroundColor White
if ($fail -eq 0) {
    Write-Host "全部通过 ✅  可以提交/合并" -ForegroundColor Green
} else {
    Write-Host "有 $fail 项不通过 ❌  先修或回滚，别往下走" -ForegroundColor Red
}
exit $fail
