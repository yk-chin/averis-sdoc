"""
ROI model - standard library only. Reads docs/roi_inputs.json, computes three scenarios, and rewrites the
table between <!-- ROI:BEGIN --> and <!-- ROI:END --> in docs/ROI.md (or prints it with --print).

Formulas (per docs/ROI.md; do not change one without the other):
    L_man  = V * t_h/60 * c_h                      manual labour per day
    L_sys  = V * c_sys                             system cost per day
    L_esc  = V * r_esc * t_r/60 * c_h              reviewing escalations per day
    L_fa   = V * r_fa * (t_fa/60 * c_h + c_fa)     dismissing false alarms per day (+ external cost if they reach the carrier)
    S_lab  = (L_man - (L_sys + L_esc + L_fa)) * D  labour saving per year
    E_man  = N_esc if given else V * p_d * m_h * D errors escaping the manual process per year
    E_sys  = V * p_d * m_s * D                     errors escaping the system per year (silent misses)
    S_risk = max(0, E_man - E_sys) * c_miss        risk saving per year
    S_tot  = S_lab + S_risk
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
INPUTS = ROOT / "docs" / "roi_inputs.json"
DOC = ROOT / "docs" / "ROI.md"
KEYS = ["V", "D", "t_h", "c_h", "p_d", "m_h", "N_esc", "m_s", "c_miss", "r_esc", "t_r", "r_fa", "t_fa", "c_fa", "c_sys"]


def compute(x: dict) -> dict:
    """x maps symbol -> value (N_esc may be None). Returns every intermediate so the table can show its work."""
    V, D, t_h, c_h, p_d, m_h = x["V"], x["D"], x["t_h"], x["c_h"], x["p_d"], x["m_h"]
    L_man = V * t_h / 60 * c_h
    L_sys = V * x["c_sys"]
    L_esc = V * x["r_esc"] * x["t_r"] / 60 * c_h
    L_fa = V * x["r_fa"] * (x["t_fa"] / 60 * c_h + x["c_fa"])
    S_lab = (L_man - (L_sys + L_esc + L_fa)) * D
    E_man = x["N_esc"] if x.get("N_esc") is not None else V * p_d * m_h * D
    E_sys = V * p_d * x["m_s"] * D
    S_risk = max(0.0, E_man - E_sys) * x["c_miss"]
    return {"L_man": L_man, "L_sys": L_sys, "L_esc": L_esc, "L_fa": L_fa, "S_lab": S_lab,
            "E_man": E_man, "E_sys": E_sys, "S_risk": S_risk, "S_tot": S_lab + S_risk}


def load() -> tuple[list[str], dict[str, dict]]:
    doc = json.loads(INPUTS.read_text(encoding="utf-8"))
    scen = doc["scenarios"]
    per = {s: {k: doc["inputs"][k]["values"][i] for k in KEYS} for i, s in enumerate(scen)}
    return scen, per


def rm(v: float) -> str:
    return f"RM {v:,.0f}" if v >= 0 else f"−RM {abs(v):,.0f}"


def render(scen: list[str], per: dict[str, dict]) -> str:
    doc = json.loads(INPUTS.read_text(encoding="utf-8"))
    res = {s: compute(per[s]) for s in scen}
    lines = ["| | " + " | ".join(scen) + " | source |", "|---|" + "---|" * (len(scen) + 1)]
    for k in KEYS:
        vals = " | ".join("–" if per[s][k] is None else f"{per[s][k]:g}" for s in scen)
        lines.append(f"| `{k}` ({doc['inputs'][k]['unit']}) | {vals} | {doc['inputs'][k]['source']} |")
    lines.append("| **Manual labour / day** `L_man` | " + " | ".join(rm(res[s]["L_man"]) for s in scen) + " | formula |")
    lines.append("| System + escalations + false alarms / day | " + " | ".join(rm(res[s]["L_sys"] + res[s]["L_esc"] + res[s]["L_fa"]) for s in scen) + " | formula |")
    lines.append("| **Labour saving / year** `S_lab` | " + " | ".join(rm(res[s]["S_lab"]) for s in scen) + " | formula |")
    lines.append("| Errors escaping manual / year `E_man` | " + " | ".join(f"{res[s]['E_man']:,.1f}" for s in scen) + " | formula or Averis N_esc |")
    lines.append("| Errors escaping the system / year `E_sys` | " + " | ".join(f"{res[s]['E_sys']:,.1f}" for s in scen) + " | formula |")
    lines.append("| **Risk saving / year** `S_risk` | " + " | ".join(rm(res[s]["S_risk"]) for s in scen) + " | formula |")
    lines.append("| **Total / year** `S_tot` | " + " | ".join(f"**{rm(res[s]['S_tot'])}**" for s in scen) + " | |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="print the table instead of rewriting docs/ROI.md")
    a = ap.parse_args()
    scen, per = load()
    table = render(scen, per)
    if a.print:
        print(table); return
    text = DOC.read_text(encoding="utf-8")
    new = re.sub(r"<!-- ROI:BEGIN -->.*?<!-- ROI:END -->", "<!-- ROI:BEGIN -->\n" + table + "\n<!-- ROI:END -->", text, flags=re.S)
    if new == text and "<!-- ROI:BEGIN -->" not in text:
        raise SystemExit("docs/ROI.md has no <!-- ROI:BEGIN --> / <!-- ROI:END --> markers")
    DOC.write_text(new, encoding="utf-8")
    for s in scen:
        r = compute(per[s]); print(f"{s:13} S_lab {r['S_lab']:>12,.0f}  S_risk {r['S_risk']:>12,.0f}  S_tot {r['S_tot']:>12,.0f}")
    print(f"-> {DOC}")


if __name__ == "__main__":
    main()
