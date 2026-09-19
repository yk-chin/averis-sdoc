#!/usr/bin/env bash
set -e
python3 -m pip install --upgrade pip
python3 -m pip install openpyxl python-docx pdfplumber pytest google-genai pydantic
python3 -m pytest tests/ -q
echo "Done. 1) Unzip the bundle into ./data  2) python3 pipeline/run.py ./data submission.json"
