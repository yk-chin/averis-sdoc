#!/usr/bin/env bash
set -e
python3 -m pip install --upgrade pip
python3 -m pip install openpyxl python-docx pdfplumber pytest google-genai pydantic
python3 -m pytest tests/ -q
echo "✅ 完成。1) 解压 bundle 到 ./data  2) python3 pipeline/run.py ./data submission.json"
