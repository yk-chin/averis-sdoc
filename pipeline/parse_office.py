"""
Office / PDF 附件解析
=====================
三种真实形态，各有陷阱：
  XLSX  两列键值表
  DOCX  表格，标签带中文括注 —— "Shipper (Principal or Seller) (发货人)"
  PDF   标签与值同行但**没有冒号** —— "Shipper APRIL FINE PAPER TRADING"，地址续行

策略：全部先归一成 "Label: Value" 文本，再复用同一套 resolve_label / 解析逻辑。
这样只有一条字段解析路径，只需维护一张别名表。
"""
from __future__ import annotations
import re, warnings, pathlib
warnings.filterwarnings("ignore")

from shipdoc_core.fields import FIELDS

# 去掉 CJK 括注："Shipper (Principal or Seller) (发货人)" → "Shipper (Principal or Seller)"
CJK_PAREN = re.compile(r"[（(]\s*[\u4e00-\u9fff][^)）]*[)）]")

# PDF 无冒号：按已知标签做行首前缀匹配。长标签优先，避免 "Shipper" 抢走 "Shipper/Exporter"
_ALL_LABELS: list[str] = sorted(
    {a for f in FIELDS for a in f.aliases} | {n for f in FIELDS for n in f.near_miss_labels},
    key=len, reverse=True,
)
_LABEL_PREFIX = re.compile(
    r"^\s*(" + "|".join(re.escape(l).replace(r"\ ", r"\s+") for l in _ALL_LABELS) + r")\s*[:\-]?\s+(.*)$",
    re.I,
)


CJK_ANY = re.compile(r"[\u3000-\u9fff\uff00-\uffef]+")


def _clean_label(s: str) -> str:
    """先去 CJK 括注，再去内联 CJK。真实数据两种都有。"""
    s = CJK_PAREN.sub("", str(s))
    s = CJK_ANY.sub("", s)
    return re.sub(r"\s+", " ", s).strip().rstrip(":").strip()


def xlsx_to_kv_text(path) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    lines = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [c for c in row if c is not None and str(c).strip()]
            if len(cells) >= 2:
                lines.append(f"{_clean_label(cells[0])}: {cells[1]}")
            elif len(cells) == 1:
                lines.append(str(cells[0]))
    return "\n".join(lines)


def docx_to_kv_text(path) -> str:
    import docx
    d = docx.Document(str(path))
    lines = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if len(cells) >= 2:
                # 单元格内换行是地址续行，取第一行作为主体
                value = cells[1].splitlines()[0].strip()
                lines.append(f"{_clean_label(cells[0])}: {value}")
            elif len(cells) == 1:
                lines.append(cells[0].splitlines()[0])
    return "\n".join(lines)


def pdf_to_kv_text(path) -> str:
    import pdfplumber
    out = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                m = _LABEL_PREFIX.match(line)
                if m:
                    out.append(f"{_clean_label(m.group(1))}: {m.group(2).strip()}")
                else:
                    out.append(line)        # 保留原行，供文档类型指纹识别
    return "\n".join(out)


def office_to_text(path) -> str | None:
    """把 pdf/docx/xlsx 转成 'Label: Value' 文本。失败返回 None → 上报 unreadable。"""
    p = pathlib.Path(path)
    try:
        ext = p.suffix.lower()
        if ext == ".xlsx":
            return xlsx_to_kv_text(p)
        if ext == ".docx":
            return docx_to_kv_text(p)
        if ext == ".pdf":
            txt = pdf_to_kv_text(p)
            # 纯图扫描件：pdfplumber 抽不出文字 → 真正的 unreadable，交 OCR/Vision
            return txt if txt and len(txt.strip()) > 40 else None
        return None
    except Exception:
        return None
