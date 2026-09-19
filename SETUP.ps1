# One-shot setup for the Averis pipeline (Windows PowerShell)
# Usage: right-click this folder > "Open in Terminal", then run  .\SETUP.ps1
$ErrorActionPreference = "Stop"
Write-Host "> Installing dependencies..." -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install openpyxl python-docx pdfplumber pytest google-genai pydantic
Write-Host "> Running unit tests..." -ForegroundColor Cyan
python -m pytest tests/ -q
Write-Host ""
Write-Host "Done. Next:" -ForegroundColor Green
Write-Host "  1) Unzip the organiser's bundle into  .\data\"
Write-Host "  2) python pipeline\run.py .\data submission.json"
Write-Host "  3) Score: python scripts_eval.py .\data --score-cli <path>\score_cli.py --gt <path>\ground_truth.json"
