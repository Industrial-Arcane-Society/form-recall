# 01 安装 AI Modeler 依赖。Agent/CADPlan 的本地实体建模需要 OCCT BREP。
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

Write-Host "`n== DeepSeek Harness Agent SDK（多模态工具循环）==" -ForegroundColor Cyan
python -m pip install --no-input --pre "deepseek-harness-sdk==0.1.5rc1"
python -c 'import deepseek_harness; print("DeepSeek Harness SDK 可用:", deepseek_harness.__file__)'

Write-Host "`n== DeepSeek Harness DSH 0.2.0-rc.2 runtime（Cordis Agent）==" -ForegroundColor Cyan
$dshRuntimeRoot = Join-Path $env:LOCALAPPDATA "AI Modeler\dsh-cli"
npm install --prefix $dshRuntimeRoot --no-audit --no-fund "@deepseek-ai/dsh@0.2.0-rc.2"
if ($LASTEXITCODE -ne 0) { throw "DSH 0.2.0-rc.2 runtime 安装失败。" }
$dshCli = Join-Path $dshRuntimeRoot "node_modules\@deepseek-ai\dsh\lib\bin.js"
node $dshCli --version
if ($LASTEXITCODE -ne 0) { throw "无法启动已安装的 DSH 0.2.0-rc.2 runtime。" }

Write-Host "`n== OpenCASCADE BREP（CADPlan 与 DSH 建模必需）==" -ForegroundColor Cyan
Write-Host "安装目标解释器："
python -c "import sys; print(sys.executable)"
python -m pip install --no-input -r requirements-geometry.txt
if ($LASTEXITCODE -ne 0) { throw "OpenCASCADE 安装失败；已停止，Agent CADPlan 不会退回 BSP 网格。" }
python -c "import importlib.metadata as m; from ai_modeler.core.geometry import occ_kernel as k; assert k.available(), k.status(); print('BREP kernel:', k.status()); print('runtime:', __import__('sys').executable); assert m.version('cadquery-ocp-novtk') == '7.9.3.1.1'"
if ($LASTEXITCODE -ne 0) { throw "当前 Python 未能导入指定版本的 OCCT；请确认应用也由上面的同一解释器启动。" }

Write-Host "`n== 自检 ==" -ForegroundColor Cyan
python -m ai_modeler --selftest
