"""
扰动测试 (perturbation test) —— 1.0 是泛化还是拟合？
====================================================
在**不改变语义**的前提下扰动数据集副本，重跑 pipeline，用 /submit 打分。
分数明显下跌的那一项，就是真正的脆弱点。

用法：  python scripts_perturb.py            # 跑全部
        python scripts_perturb.py P3 P5      # 只跑指定项
产出：  evals/perturbation.json（每项的分数 + 覆盖率）

约束（硬性）：
  - 原始 data/ 只读；副本写到 .cache/perturb/<P>/
  - 不读 ground_truth；只读 inbox / attachments 和 /submit 返回的聚合分数
  - 不写 evals/history.jsonl（那是主线进度指标，扰动跑分不算进去）
  - PDF 附件不改（28 个），每项报告实际覆盖率

扰动项：
  P1  字段标签换成同义写法（Port of Loading → Load Port → POL …），SI/BL 各用不同别名
  P2  公司后缀写法（CO., LTD ↔ Co Ltd ↔ Company Limited），SI 用变体 A、BL 用变体 B
  P3  重量单位：SI 换算成 MT（2 位小数），BL 换算成 LBS（1 位小数）
  P4a 港口：SI 去掉 UN/LOCODE 只留名字，BL 不动
  P4b 港口：BL 只留 UN/LOCODE —— 仅当名字与代码在真实 LOCODE 表里一致（避免抹掉主办方埋的缺陷）
  P5  附件文件名去掉 _SI / _BL 标记（email_001_SI.txt → email_001_doc_a.txt）
"""
from __future__ import annotations
import json, pathlib, re, shutil, subprocess, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from shipdoc_core.fields import FIELDS, FIELD_BY_KEY, FieldKind, resolve_label
from pipeline.parse_doc import LABEL_LINE

DATA = ROOT / "data"
WORK = ROOT / ".cache" / "perturb"
SERVER = "http://localhost:8080"
PY = sys.executable

# 真实 UN/LOCODE（行业知识，非数据集反推）。P4b 只在 (code, name) 一致时才把 BL 改成纯代码。
REAL_LOCODE = {
    "MYPKG": "PORT KLANG", "SGSIN": "SINGAPORE", "INNSA": "NHAVA SHEVA", "VNSGN": "HO CHI MINH CITY",
    "USNYC": "NEW YORK", "KRPUS": "BUSAN", "CNSHA": "SHANGHAI", "CNNTG": "NANTONG", "PECLL": "CALLAO",
    "PKKHI": "KARACHI", "TRMER": "MERSIN", "KEMBA": "MOMBASA", "AUFRE": "FREMANTLE", "GNCKY": "CONAKRY",
    "LTKLJ": "KLAIPEDA", "NGAPP": "APAPA", "USSAV": "SAVANNAH", "KRPTK": "PYEONGTAEK", "MMRGN": "YANGON",
    "USBAL": "BALTIMORE", "AUBNE": "BRISBANE", "USLGB": "LONG BEACH", "ILASH": "ASHDOD", "PLGDN": "GDANSK",
    "CLVAP": "VALPARAISO", "SIKOP": "KOPER", "JOAQB": "AQABA", "USHOU": "HOUSTON", "PHCEB": "CEBU",
    "INTUT": "TUTICORIN", "IDBUA": "BUATAN",
}


# ----------------------------------------------------------------------------
# 单行变换：(label, value, side, seed) → (label, value)。side ∈ {"SI","BL"}
# ----------------------------------------------------------------------------
def _title(s: str) -> str:
    return " ".join(w.capitalize() if w.isalpha() else w for w in s.split())


def p1_label(label, value, side, seed):
    key, near = resolve_label(label)
    if key is None or near:
        return label, value
    aliases = [a for a in FIELD_BY_KEY[key].aliases if a != label.lower()]
    # SI/BL 取不同别名；用 seed 让每封邮件轮换
    pick = aliases[(seed + (0 if side == "SI" else 3)) % len(aliases)]
    return _title(pick), value


_SUFFIX_A = [(r"\bCO\.?,?\s*LTD\.?", "Co Ltd"), (r"\bPTE\.?\s*LTD\.?", "Pte Ltd"),
             (r"\bSDN\.?\s*BHD\.?", "Sdn. Bhd."), (r"\bLLC\b", "L.L.C."), (r"\bGMBH\b", "GmbH"),
             (r"\bPTY\.?\s*LTD\.?", "Pty. Ltd.")]
_SUFFIX_B = [(r"\bCO\.?,?\s*LTD\.?", "Company Limited"), (r"\bPTE\.?\s*LTD\.?", "Pte. Limited"),
             (r"\bSDN\.?\s*BHD\.?", "Sendirian Berhad"), (r"\bLLC\b", "LLC"), (r"\bGMBH\b", "GmbH"),
             (r"\bPTY\.?\s*LTD\.?", "Pty Limited")]


def p2_suffix(label, value, side, seed):
    key, near = resolve_label(label)
    if key is None or near or FIELD_BY_KEY[key].kind is not FieldKind.PARTY:
        return label, value
    table = _SUFFIX_A if side == "SI" else _SUFFIX_B
    v = value
    for pat, rep in table:
        v = re.sub(pat, rep, v, flags=re.I)
    return label, v


def p3_weight(label, value, side, seed):
    key, near = resolve_label(label)
    if key is None or near or FIELD_BY_KEY[key].kind is not FieldKind.WEIGHT_KG:
        return label, value
    m = re.search(r"\d[\d,]*(?:\.\d+)?", value)
    if not m:
        return label, value
    kg = float(m.group(0).replace(",", ""))
    if side == "SI":
        mt = kg / 1000
        new = f"{mt:g} MT" if mt == int(mt) else f"{mt:.2f} MT"      # 2 位小数避免千分位歧义
    else:
        new = f"{kg * 2.20462262:,.1f} LBS"
    return label, new


def p4a_port_si_name_only(label, value, side, seed):
    key, near = resolve_label(label)
    if key is None or near or FIELD_BY_KEY[key].kind is not FieldKind.PORT or side != "SI":
        return label, value
    return label, re.sub(r"\s*\([A-Z]{2}[A-Z0-9]{3}\)\s*", "", value).strip()


def p4b_port_bl_code_only(label, value, side, seed):
    key, near = resolve_label(label)
    if key is None or near or FIELD_BY_KEY[key].kind is not FieldKind.PORT or side != "BL":
        return label, value
    m = re.search(r"\(([A-Z]{2}[A-Z0-9]{3})\)", value)
    if not m:
        return label, value
    code = m.group(1)
    name = re.sub(r"\s*\(.*?\)\s*", " ", value).split(",")[0].strip().upper()
    if REAL_LOCODE.get(code) and REAL_LOCODE[code] in name:      # 名字与代码一致才换，保住缺陷语义
        return label, code
    return label, value


PERTURBATIONS = {
    "P1": ("字段标签同义轮换", p1_label),
    "P2": ("公司后缀写法 (SI 变体A / BL 变体B)", p2_suffix),
    "P3": ("重量单位 (SI→MT / BL→LBS)", p3_weight),
    "P4a": ("港口：SI 只留名字", p4a_port_si_name_only),
    "P4b": ("港口：BL 只留 LOCODE（仅一致对）", p4b_port_bl_code_only),
    "P5": ("附件文件名去掉 _SI/_BL", None),
}


# ----------------------------------------------------------------------------
# 把变换应用到一份附件
# ----------------------------------------------------------------------------
def _side(name: str) -> str:
    return "SI" if "_SI." in name.upper() else "BL"


def _seed(name: str) -> int:
    m = re.search(r"email_(\d+)", name)
    return int(m.group(1)) if m else 0


def apply_txt(path: pathlib.Path, fn) -> int:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out, changed = [], 0
    for line in lines:
        m = LABEL_LINE.match(line)
        if m:
            lab, val = m.group(1).strip(), m.group(2).strip()
            nl, nv = fn(lab, val, _side(path.name), _seed(path.name))
            if (nl, nv) != (lab, val):
                changed += 1
                line = f"{nl}: {nv}"
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def apply_xlsx(path: pathlib.Path, fn) -> int:
    import openpyxl
    wb = openpyxl.load_workbook(path)
    changed = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            cells = [c for c in row if c.value is not None and str(c.value).strip()]
            if len(cells) >= 2:
                lab, val = str(cells[0].value).strip().rstrip(":"), str(cells[1].value).strip()
                nl, nv = fn(lab, val, _side(path.name), _seed(path.name))
                if (nl, nv) != (lab, val):
                    cells[0].value, cells[1].value = nl, nv; changed += 1
    wb.save(path)
    return changed


def apply_docx(path: pathlib.Path, fn) -> int:
    import docx
    d = docx.Document(str(path))
    changed = 0
    for p in d.paragraphs:
        m = LABEL_LINE.match(p.text)
        if m:
            nl, nv = fn(m.group(1).strip(), m.group(2).strip(), _side(path.name), _seed(path.name))
            if (nl, nv) != (m.group(1).strip(), m.group(2).strip()):
                p.text = f"{nl}: {nv}"; changed += 1
    for t in d.tables:
        for row in t.rows:
            cells = [c for c in row.cells if c.text.strip()]
            if len(cells) >= 2:
                lab = cells[0].text.strip().rstrip(":")
                first, *rest = cells[1].text.split("\n")
                nl, nv = fn(lab, first.strip(), _side(path.name), _seed(path.name))
                if (nl, nv) != (lab, first.strip()):
                    cells[0].text = nl; cells[1].text = "\n".join([nv, *rest]); changed += 1
    d.save(str(path))
    return changed


def build_copy(name: str, fn) -> dict:
    dst = WORK / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(DATA, dst)
    cov = {"txt": [0, 0], "xlsx": [0, 0], "docx": [0, 0], "pdf": [0, 0], "lines_changed": 0}
    if fn is None:                                             # P5：改文件名 + inbox 引用
        renamed = 0
        for p in sorted((dst / "attachments").glob("*")):
            new = p.name.replace("_SI.", "_doc_a.").replace("_BL.", "_doc_b.")
            if new != p.name:
                p.rename(p.with_name(new)); renamed += 1
        for f in (dst / "inbox").glob("*.json"):
            e = json.load(open(f, encoding="utf-8"))
            e["attachments"] = [a.replace("_SI.", "_doc_a.").replace("_BL.", "_doc_b.") for a in e.get("attachments") or []]
            json.dump(e, open(f, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        cov["renamed"] = renamed
        return cov
    for p in sorted((dst / "attachments").glob("*")):
        ext = p.suffix.lstrip(".").lower()
        cov[ext][1] += 1
        if ext == "txt":
            n = apply_txt(p, fn)
        elif ext == "xlsx":
            n = apply_xlsx(p, fn)
        elif ext == "docx":
            n = apply_docx(p, fn)
        else:
            continue                                            # pdf 不改
        if n:
            cov[ext][0] += 1; cov["lines_changed"] += n
    return cov


# ----------------------------------------------------------------------------
def run_and_score(data_dir: pathlib.Path, out: pathlib.Path) -> dict:
    r = subprocess.run([PY, str(ROOT / "pipeline" / "run.py"), str(data_dir), str(out)],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-800:])
    sub = json.load(open(out, encoding="utf-8"))
    req = urllib.request.Request(f"{SERVER}/submit", data=json.dumps(sub).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    s = json.loads(urllib.request.urlopen(req, timeout=120).read())
    return {"final_score": s["final_score"], "macro_f1": s["stage1"]["macro_f1"],
            "defect_f1": s["stage3"]["defect_f1"], "field_f1": s["stage3"]["field_f1"],
            "end_to_end": s["end_to_end"]["rate"],
            "esc_precision": s["reliability"]["escalation_precision"],
            "esc_recall": s["reliability"]["escalation_recall"],
            "pred_review": s["reliability"]["pred_review"]}


def main(selected: list[str]):
    WORK.mkdir(parents=True, exist_ok=True)
    print("baseline（未扰动）…")
    base = run_and_score(DATA, WORK / "baseline_submission.json")
    results = {"baseline": base}
    rows = [("baseline", "未扰动", base, {})]
    for name, (desc, fn) in PERTURBATIONS.items():
        if selected and name not in selected:
            continue
        print(f"{name} {desc} …", flush=True)
        cov = build_copy(name, fn)
        try:
            sc = run_and_score(WORK / name, WORK / f"{name}_submission.json")
        except RuntimeError as e:
            sc = {"error": str(e)}
        results[name] = {"desc": desc, "coverage": cov, **sc}
        rows.append((name, desc, sc, cov))

    print("\n| 扰动 | 说明 | final | Δ | macro_f1 | defect_f1 | field_f1 | end_to_end | esc_P | esc_R | 覆盖 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, desc, sc, cov in rows:
        if "error" in sc:
            print(f"| {name} | {desc} | ERROR | | | | | | | | {sc['error'][:60]} |"); continue
        d = sc["final_score"] - base["final_score"]
        if cov.get("renamed") is not None:
            c = f"{cov['renamed']} 个文件改名"
        elif cov:
            c = " ".join(f"{k}:{v[0]}/{v[1]}" for k, v in cov.items() if isinstance(v, list) and v[1])
            c += f" ({cov['lines_changed']} 行)"
        else:
            c = "—"
        print(f"| {name} | {desc} | {sc['final_score']:.4f} | {d:+.4f} | {sc['macro_f1']:.4f} | {sc['defect_f1']:.4f} | "
              f"{sc['field_f1']:.3f} | {sc['end_to_end']:.4f} | {sc['esc_precision']:.3f} | {sc['esc_recall']:.3f} | {c} |")
    (ROOT / "evals").mkdir(exist_ok=True)
    json.dump(results, open(ROOT / "evals" / "perturbation.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\n写入 evals/perturbation.json；副本在 {WORK}")


if __name__ == "__main__":
    main(sys.argv[1:])
