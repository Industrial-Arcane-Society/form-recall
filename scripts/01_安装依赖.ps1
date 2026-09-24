# 01 安装依赖（可选；核心功能不需要任何第三方库）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\01_安装依赖.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"

Write-Host "== Python 版本 ==" -ForegroundColor Cyan
python -V
python -c "import importlib.util as u; print('numpy:', bool(u.find_spec('numpy')), ' scipy:', bool(u.find_spec('scipy')), ' PIL:', bool(u.find_spec('PIL')), ' cv2:', bool(u.find_spec('cv2')), ' OCP:', bool(u.find_spec('OCP')))"

Write-Host "`n== 基础依赖（numpy/scipy/Pillow/opencv/matplotlib/python-docx）==" -ForegroundColor Cyan
python -m pip install --no-input numpy scipy pillow opencv-python matplotlib python-docx

Write-Host "`n== OpenCASCADE（可选，多孔/复杂件更快更准）==" -ForegroundColor Cyan
if (Test-Path "tools\fetch_ocp_wheel.py") {
    python tools\fetch_ocp_wheel.py
    $whl = Get-ChildItem "_wheels" -Filter "cadquery_ocp*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($whl) {
        python -m pip install --no-input --no-index --no-deps --find-links "_wheels" cadquery-ocp-proxy cadquery-ocp
        $vtk = Get-ChildItem "_wheels" -Filter "vtk-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($vtk) { python -m pip install --no-input --no-index --no-deps $vtk.FullName }
    }
}
python -c "import importlib.util as u; print('OCP 可用:', bool(u.find_spec('OCP')))"

Write-Host "`n== 自检 ==" -ForegroundColor Cyan
python -m ai_modeler --selftest
