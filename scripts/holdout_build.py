"""
Build the independent hold-out set: evals/holdout/{inbox,attachments,gold.json}
==============================================================================
Forty emails we author ourselves, deliberately outside the organiser's distribution: other company-suffix
systems (K.K., GmbH & Co. KG, A.Ş., S.A. de C.V., Pty Ltd, S.p.A.), ports absent from our LOCODE table,
label vocabularies not in our alias list, mixed-language labels, weight/count notations we have not seen,
xlsx/docx attachments, untagged filenames, and eight escalation cases. The gold labels are written here,
from the business truth of each case, before the pipeline is run on them (scripts/holdout_eval.py).

Protocol (EVAL.md): this set is never used to tune anything. Whatever it scores is reported as is.
Deterministic: re-running produces byte-identical files.
"""
from __future__ import annotations

import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "holdout"
rng = random.Random(4242)

# ----------------------------------------------------------------------------- vocabulary (all invented)
PARTIES = [
    ("Kaisei Kogyo K.K.", "KAISEI KOGYO KABUSHIKI KAISHA"),
    ("Müller Papier GmbH & Co. KG", "MUELLER PAPIER GMBH & CO. KG"),
    ("Anadolu Kağıt Sanayi A.Ş.", "ANADOLU KAGIT SANAYI A.S."),
    ("Papeles del Pacífico S.A. de C.V.", "PAPELES DEL PACIFICO SA DE CV"),
    ("Southern Cross Fibre Pty Ltd", "SOUTHERN CROSS FIBRE PTY. LTD."),
    ("Nordvest Emballasje AS", "NORDVEST EMBALLASJE AS"),
    ("Cartiere Adriatiche S.p.A.", "CARTIERE ADRIATICHE SPA"),
    ("Maple Leaf Packaging Inc.", "MAPLE LEAF PACKAGING INCORPORATED"),
    ("Lotus Paper Trading Co., Ltd.", "LOTUS PAPER TRADING COMPANY LIMITED"),
    ("Delta Nile Trading LLC", "DELTA NILE TRADING L.L.C."),
    ("Harbour View Logistics B.V.", "HARBOUR VIEW LOGISTICS BV"),
    ("Qilin Pulp & Paper Ltd.", "QILIN PULP AND PAPER LIMITED"),
]
ADDR = ["12 Marina Way, #05-01", "Industriestrasse 4, 20457", "Liman Cad. No: 8, Mersin", "Av. Insurgentes Sur 1602",
        "45 Wharf Road, Port Melbourne", "Havnegata 9, 6001 Ålesund", "Via del Porto 22, 16126 Genova", "800 Bay St, Toronto"]
PORTS = [("Haiphong", "VNHPH"), ("Chittagong", "BDCGP"), ("Santos", "BRSSZ"), ("Le Havre", "FRLEH"), ("Antwerp", "BEANR"),
         ("Colombo", "LKCMB"), ("Durban", "ZADUR"), ("Manzanillo", "MXZLO"), ("Melbourne", "AUMEL"), ("Kobe", "JPUKB"),
         ("Genoa", "ITGOA"), ("Alexandria", "EGALY")]
SENDERS = ["docs@kaisei-kogyo.example", "export@muellerpapier.example", "ops@anadolukagit.example", "trafico@papelespacifico.example",
           "shipping@southerncrossfibre.example", "logistikk@nordvest.example", "spedizioni@cartiereadriatiche.example",
           "traffic@mapleleafpkg.example", "export@lotuspaper.example", "docs@deltanile.example"]

# label styles: A standard, B qualified, C synonyms, D mixed-language, E abbreviations (not in our alias list)
STYLES = {
    "A": ["Shipper", "Consignee", "Notify Party", "Port of Loading", "Port of Discharge", "Number of Containers", "Gross Weight (KGS)"],
    "B": ["Shipper/Exporter", "Consignee (complete name and address)", "Notify Party (if different)", "Port of Loading (POL)",
          "Port of Discharge (POD)", "Total Containers", "Gross Wt"],
    "C": ["Exporter", "Consigned To", "Also Notify", "Loading Port", "Discharging Port", "Qty of Containers", "Total Gross Weight"],
    "D": ["Shipper 发货人", "Consignee 收货人", "Notify Party 通知方", "Port of Loading 装货港", "Port of Discharge 卸货港",
          "Container Count 箱量", "Gross Weight 毛重 (KGS)"],
    "E": ["Shpr", "Cnee", "Ntfy Party", "Port of Loading", "Port of Discharge", "No. of Cntrs", "G.Wt. (kgs)"],
}
KEYS = ["shipper", "consignee", "notify_party", "port_of_loading", "port_of_discharge", "container_count", "gross_weight_kg"]


def party(i, variant, with_addr=True):
    name = PARTIES[i][variant]
    return f"{name} | {rng.choice(ADDR)}" if with_addr and variant == 0 else name


def port(i, variant):
    name, code = PORTS[i]
    return [f"{name} ({code})", name.upper(), f"{name.upper()}, {code}", f"{code}"][variant]


def weight(kg, variant):
    return [f"{kg:,} KG", f"{kg/1000:.3f} MT", f"{kg*2.20462:,.1f} LBS", f"{kg:,}.00 KGS", f"{kg} kgs", f"{kg/1000:.2f} Tonnes"][variant]


def count(n, variant):
    return [str(n), f"{n} x 40'HC", f"{n}x20'GP", f"{n} containers", f"{n} X 40'HC"][variant]


def doc_text(kind, labels, values):
    head = "SHIPPING INSTRUCTION" if kind == "SI" else "BILL OF LADING (DRAFT)"
    lines = [head, f"Booking Ref: HO-{rng.randint(10000, 99999)}", ""]
    lines += [f"{lab}: {val}" for lab, val in zip(labels, values)]
    lines += ["", f"Vessel/Voyage: MV HOLDOUT STAR {rng.randint(1, 99)}W", f"Freight: {rng.choice(['PREPAID', 'COLLECT'])}"]
    return "\n".join(lines) + "\n"


def write_xlsx(path, kind, labels, values):
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = kind
    ws.append(["SHIPPING INSTRUCTION" if kind == "SI" else "BILL OF LADING (DRAFT)", ""])
    for lab, val in zip(labels, values):
        ws.append([lab, val])
    wb.save(path)


def write_docx(path, kind, labels, values):
    from docx import Document
    d = Document(); d.add_heading("SHIPPING INSTRUCTION" if kind == "SI" else "BILL OF LADING (DRAFT)", 1)
    for lab, val in zip(labels, values):
        d.add_paragraph(f"{lab}: {val}")
    d.save(path)


def main():
    inbox, att = OUT / "inbox", OUT / "attachments"
    for d in (inbox, att):
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*"):
            f.unlink()
    gold, n = {}, 0

    def emit(email, gold_rec, files=()):
        nonlocal n
        n += 1
        eid = f"holdout_{n:03d}"
        rels = []
        for name, content in files:
            p = att / f"{eid}_{name}"
            if callable(content):
                content(p)
            elif isinstance(content, bytes):
                p.write_bytes(content)
            else:
                p.write_text(content, encoding="utf-8")
            rels.append(f"attachments/{p.name}")
        rec = {"email_id": eid, "from": email.get("from", rng.choice(SENDERS)), "subject": email["subject"],
               "body": email["body"], "attachments": rels}
        (inbox / f"{eid}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        gold[eid] = {"category": gold_rec[0], "status": gold_rec[1], "review_reason": gold_rec[2],
                     "has_defect": bool(gold_rec[3]), "defect_fields": sorted(gold_rec[3])}

    bodies_compare = [
        "Hi team,\n\nPlease find attached the SI and the draft BL for booking {b}. Kindly compare and confirm the draft is in order before cut-off.\n\nRegards",
        "Dear all,\n\nAttached: our shipping instruction and the carrier's draft B/L. Please check the details match and revert with any discrepancy today.\n\nThanks",
        "Hello,\n\nDraft BL received from the line - attached together with the SI. Please verify shipper/consignee/notify, ports, container qty and gross weight.\n\nBest regards",
        "Hi,\n\nSee attached SI + draft BL for shipment {b}. Confirm or advise amendments.\n\nRgds",
    ]

    # ---------------------------------------------------------------- 10 x OK (formatting-only differences)
    ok_specs = [  # (style_si, style_bl, party variants, port variants, weight variants, count variants, attach kind)
        ("A", "B", (0, 1), (0, 1), (0, 1), (1, 0), "txt"), ("C", "A", (0, 1), (2, 1), (3, 4), (0, 3), "txt"),
        ("D", "A", (0, 0), (0, 3), (1, 0), (1, 1), "txt"), ("B", "C", (0, 1), (1, 1), (2, 0), (2, 0), "txt"),
        ("A", "A", (1, 1), (0, 2), (5, 0), (0, 0), "xlsx"), ("A", "D", (0, 1), (1, 0), (0, 2), (4, 1), "docx"),
        ("E", "A", (0, 1), (0, 1), (0, 0), (0, 0), "txt"), ("B", "B", (0, 0), (2, 0), (3, 1), (3, 0), "untagged"),
        ("C", "D", (0, 1), (0, 1), (1, 3), (1, 0), "txt"), ("A", "C", (0, 1), (3, 1), (0, 5), (0, 4), "xlsx"),
    ]
    for k, (ssi, sbl, pv, portv, wv, cv, kind) in enumerate(ok_specs):
        sh, co, no = rng.sample(range(len(PARTIES)), 3); pol, pod = rng.sample(range(len(PORTS)), 2)
        kg = rng.randint(8, 240) * 1000 + rng.randint(0, 999); cnt = rng.randint(1, 24)
        si_vals = [party(sh, pv[0]), party(co, pv[0]), party(no, pv[0]), port(pol, portv[0]), port(pod, portv[0]), count(cnt, cv[0]), weight(kg, wv[0])]
        bl_vals = [party(sh, pv[1], False), party(co, pv[1], False), party(no, pv[1], False), port(pol, portv[1]), port(pod, portv[1]), count(cnt, cv[1]), weight(kg, wv[1])]
        files = _files(kind, ssi, sbl, si_vals, bl_vals)
        emit({"subject": f"RE: DRAFT BL {rng.choice(['CHECK', 'FOR CONFIRMATION'])} _ HO-{40000 + k}", "body": rng.choice(bodies_compare).format(b=f"HO-{40000 + k}")},
             ("BL_COMPARISON", "OK", None, []), files)

    # ---------------------------------------------------------------- 8 x MISMATCH (real differences)
    mm_specs = [
        (["consignee"], "A", "A"), (["gross_weight_kg"], "B", "A"), (["container_count"], "A", "C"), (["port_of_loading"], "D", "A"),
        (["notify_party", "port_of_discharge"], "A", "B"), (["shipper"], "C", "A"), (["gross_weight_kg", "container_count"], "A", "A"),
        (["consignee", "notify_party"], "B", "D"),
    ]
    for k, (defects, ssi, sbl) in enumerate(mm_specs):
        sh, co, no, alt1, alt2 = rng.sample(range(len(PARTIES)), 5); pol, pod, altp = rng.sample(range(len(PORTS)), 3)
        kg = rng.randint(8, 240) * 1000; cnt = rng.randint(2, 20)
        si = {"shipper": party(sh, 0), "consignee": party(co, 0), "notify_party": party(no, 0), "port_of_loading": port(pol, 0),
              "port_of_discharge": port(pod, 0), "container_count": count(cnt, 1), "gross_weight_kg": weight(kg, 0)}
        bl = {"shipper": party(sh, 1, False), "consignee": party(co, 1, False), "notify_party": party(no, 1, False),
              "port_of_loading": port(pol, 1), "port_of_discharge": port(pod, 1), "container_count": count(cnt, 0), "gross_weight_kg": weight(kg, 1)}
        for d in defects:
            if d == "consignee": bl[d] = party(alt1, 1, False)
            elif d == "notify_party": bl[d] = party(alt2, 1, False)
            elif d == "shipper": bl[d] = party(alt1, 1, False)
            elif d == "port_of_loading": bl[d] = port(altp, 1)
            elif d == "port_of_discharge": bl[d] = port(altp, 0)
            elif d == "container_count": bl[d] = count(cnt + rng.choice([1, 2]), 0)
            elif d == "gross_weight_kg": bl[d] = weight(int(kg * rng.choice([1.06, 0.92, 1.5])), 0)
        files = _files("txt" if k % 3 else "untagged", ssi, sbl, [si[x] for x in KEYS], [bl[x] for x in KEYS])
        emit({"subject": f"DRAFT B/L _ HO-{41000 + k} _ {PORTS[pod][0].upper()}", "body": rng.choice(bodies_compare).format(b=f"HO-{41000 + k}")},
             ("BL_COMPARISON", "MISMATCH", None, defects), files)

    # ---------------------------------------------------------------- 6 x NEEDS_REVIEW
    sh, co, no = rng.sample(range(len(PARTIES)), 3); pol, pod = rng.sample(range(len(PORTS)), 2)
    base_si = [party(sh, 0), party(co, 0), party(no, 0), port(pol, 0), port(pod, 0), count(6, 1), weight(96000, 0)]
    base_bl = [party(sh, 1, False), party(co, 1, False), party(no, 1, False), port(pol, 1), port(pod, 1), count(6, 0), weight(96000, 1)]
    emit({"subject": "TO CONFIRM DOCS _ HO-42000", "body": "Hi,\n\nPlease compare the attached SI against the draft BL and confirm.\n\nThanks"},
         ("BL_COMPARISON", "NEEDS_REVIEW", "missing_attachment", []), [])                       # nothing attached
    emit({"subject": "RE: draft BL for checking _ HO-42001", "body": "Dear team,\n\nKindly check the SI vs the draft B/L attached and let us know.\n\nRegards"},
         ("BL_COMPARISON", "NEEDS_REVIEW", "missing_attachment", []), [("SI.txt", doc_text("SI", STYLES["A"], base_si))])   # BL missing
    emit({"subject": "Draft BL attached _ HO-42002", "body": "Hi,\n\nSI and draft BL attached, please verify.\n\nRgds"},
         ("BL_COMPARISON", "NEEDS_REVIEW", "unreadable", []),
         [("SI.txt", doc_text("SI", STYLES["B"], base_si)), ("BL.pdf", b"%PDF-1.5\n" + bytes(rng.randrange(256) for _ in range(900)))])   # corrupt
    invoice = "COMMERCIAL INVOICE\nInvoice No: CI-88213\nSeller: " + PARTIES[sh][0] + "\nBuyer: " + PARTIES[co][0] + "\nAmount: USD 48,200.00\n"
    emit({"subject": "Docs for HO-42003", "body": "Hello,\n\nPlease compare the SI with the draft BL attached.\n\nThanks"},
         ("BL_COMPARISON", "NEEDS_REVIEW", "wrong_doc_type", []),
         [("SI.txt", doc_text("SI", STYLES["A"], base_si)), ("BL.txt", invoice)])                 # an invoice where the BL should be
    tba = list(base_bl); tba[1] = "TBA"
    emit({"subject": "RE: BL DRAFT HO-42004", "body": "Hi,\n\nAttached SI and draft BL. Please check all details.\n\nRegards"},
         ("BL_COMPARISON", "NEEDS_REVIEW", "missing_value", []),
         [("SI.txt", doc_text("SI", STYLES["A"], base_si)), ("BL.txt", doc_text("BL", STYLES["A"], tba))])       # consignee blank
    emit({"subject": "Please compare SI and BL _ HO-42005", "body": "Team,\n\nCompare the shipping instruction and the draft bill of lading and revert.\n\nThanks"},
         ("BL_COMPARISON", "NEEDS_REVIEW", "missing_attachment", []), [("SI.txt", doc_text("SI", STYLES["C"], base_si))])
    # a request for the file is not a failed comparison
    emit({"subject": "Draft BL _ HO-42006", "body": "Hi,\n\nCould you please send us the draft BL for HO-42006 for checking once available?\n\nThanks"},
         ("BL_COMPARISON", "OK", None, []), [])

    # ---------------------------------------------------------------- 16 x other categories
    for s, b in [("SI needed _ HO-43001", "Hi,\n\nPlease send the shipping instruction for booking HO-43001 by tomorrow noon so we can submit to the carrier.\n\nThanks"),
                 ("RE: SI submission _ HO-43002", "Dear team,\n\nKindly provide the SI details (shipper, consignee, notify, cargo description) for the above booking.\n\nRegards"),
                 ("SI required for cut-off _ HO-43003", "Hello,\n\nSI cut-off is Thursday 1700. Please send the completed shipping instruction template.\n\nRgds"),
                 ("Shipping instruction _ HO-43004", "Hi,\n\nWe have not received your SI for HO-43004. Please submit at the earliest.\n\nThanks"),
                 ("SI pls _ HO-43005", "Pls send SI for the Kobe shipment, need to file by today.\n\nThx"),
                 ("Request: shipping instructions _ HO-43006", "Good morning,\n\nPlease share the shipping instructions for the two containers ex Santos.\n\nBest")]:
        emit({"subject": s, "body": b}, ("SI_REQUEST", "OK", None, []), [])
    for s, b in [("Freight invoice query _ INV-9001", "Hi,\n\nInvoice INV-9001 shows THC at USD 180 per box but the quote was USD 150. Please issue a credit note.\n\nRegards"),
                 ("RE: Invoice INV-9002 discrepancy", "Dear finance,\n\nThe local charges on invoice INV-9002 do not match the agreed tariff. Kindly check and revise.\n\nThanks"),
                 ("Invoice not received _ HO-40003", "Hello,\n\nWe have not received the freight invoice for shipment HO-40003. Please send it for payment processing.\n\nRgds"),
                 ("Payment of invoice INV-9004", "Hi,\n\nPlease confirm receipt of our payment for INV-9004 and send the official receipt.\n\nThanks"),
                 ("Query on DTHC billing", "Dear team,\n\nWhy was DTHC billed to us when terms are CFR? Please clarify the invoice.\n\nRegards")]:
        emit({"subject": s, "body": b}, ("INVOICE_QUERY", "OK", None, []), [])
    for s, b in [("Office closure notice", "Dear partners,\n\nOur office will be closed on Monday for the public holiday. Urgent matters: call the duty line.\n\nRegards"),
                 ("Vessel schedule update", "Hi all,\n\nMV HOLDOUT STAR 12W is delayed by two days; new ETA Antwerp 28 Sep. No action needed.\n\nBest"),
                 ("New contact person", "Hello,\n\nFrom next week Ms. Lim will handle your account. Her details are in the signature.\n\nThanks")]:
        emit({"subject": s, "body": b}, ("GENERAL", "OK", None, []), [])
    for s, b in [("Congratulations! You won a free shipment", "Click here to claim your prize: http://free-cargo-prize.example/claim\n\nLimited time!"),
                 ("Your mailbox is over quota", "Dear user, verify your account within 24 hours or it will be suspended: http://mail-verify.example\n")]:
        emit({"subject": s, "body": b, "from": rng.choice(["promo@free-cargo-prize.example", "admin@mail-verify.example"])}, ("SPAM", "OK", None, []), [])

    (OUT / "gold.json").write_text(json.dumps({"_protocol": "Authored by the ShipDoc team on 2026-09-20 from the business truth of each "
                                               "case, before running the pipeline on this set. Never used for tuning. Synthetic; no real "
                                               "companies or people.", **gold}, indent=2, ensure_ascii=False), encoding="utf-8")
    from collections import Counter
    print(f"{n} emails ->", OUT)
    print("categories:", dict(Counter(g['category'] for g in gold.values())))
    print("BL statuses:", dict(Counter((g['status'], g['review_reason']) for g in gold.values() if g['category'] == 'BL_COMPARISON')))


def _files(kind, ssi, sbl, si_vals, bl_vals):
    si_lab, bl_lab = STYLES[ssi], STYLES[sbl]
    if kind == "xlsx":
        return [("SI.xlsx", lambda p: write_xlsx(p, "SI", si_lab, si_vals)), ("BL.txt", doc_text("BL", bl_lab, bl_vals))]
    if kind == "docx":
        return [("SI.txt", doc_text("SI", si_lab, si_vals)), ("BL.docx", lambda p: write_docx(p, "BL", bl_lab, bl_vals))]
    if kind == "untagged":
        return [("doc1.txt", doc_text("SI", si_lab, si_vals)), ("doc2.txt", doc_text("BL", bl_lab, bl_vals))]
    return [("SI.txt", doc_text("SI", si_lab, si_vals)), ("BL.txt", doc_text("BL", bl_lab, bl_vals))]


if __name__ == "__main__":
    sys.exit(main())
