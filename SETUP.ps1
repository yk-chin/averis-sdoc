# Averis 流水线一键安装 (Windows PowerShell)
# 用法：在本文件夹右键「在终端中打开」，然后跑  .\SETUP.ps1
$ErrorActionPreference = "Stop"
Write-Host "> 安装依赖..." -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install openpyxl python-docx pdfplumber pytest google-genai pydantic
Write-Host "> 跑单元测试..." -ForegroundColor Cyan
python -m pytest tests/ -q
Write-Host ""
Write-Host "完成。接下来：" -ForegroundColor Green
Write-Host "  1) 把主办方的 bundle 解压到  .\data\"
Write-Host "  2) python pipeline\run.py .\data submission.json"
Write-Host "  3) 打分： python scripts_eval.py .\data --score-cli <路径>\score_cli.py --gt <路径>\ground_truth.json"
