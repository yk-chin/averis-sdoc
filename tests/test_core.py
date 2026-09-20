import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shipdoc_core.fields import resolve_label, FIELD_KEYS
from shipdoc_core.normalize import (normalize_party, normalize_port, normalize_count,
                                    normalize_weight_kg, ports_match)
from shipdoc_core.compare import compare_documents, compare_field, Outcome
from shipdoc_core.evaluate import (prf, classification_report, field_level_prf,
                                   calibration, optimal_threshold)

# ---------- field ontology ----------
def test_seven_fields():
    assert tuple(FIELD_KEYS) == ("shipper","consignee","notify_party","port_of_loading",
                          "port_of_discharge","container_count","gross_weight_kg")

def test_alias_resolution():
    for lab in ["Port of Loading","Load Port","POL","port  of   loading"]:
        assert resolve_label(lab) == ("port_of_loading", False)

def test_near_miss_trap():
    """Place of Receipt is not Port of Loading - the classic source of false alarms"""
    key, near = resolve_label("Place of Receipt")
    assert key == "port_of_loading" and near is True

def test_unknown_label():
    assert resolve_label("Vessel Name") == (None, False)

# ---------- normalisation ----------
def test_party_equivalence():
    pairs = [("ABC CO., LTD.","ABC Co Ltd"),
             ("Global Trading Sdn. Bhd.","GLOBAL TRADING SENDIRIAN BERHAD"),
             ("Nippon Kaisha K.K., Tokyo 100-0001","NIPPON KAISHA KK"),
             ("ACME CORP","Acme Corporation")]
    for a,b in pairs: assert normalize_party(a)==normalize_party(b), (a,b)

def test_party_difference_preserved():
    assert normalize_party("ABC CO., LTD.") != normalize_party("XYZ CO., LTD.")

def test_port_equivalence():
    for a,b in [("PORT KELANG","Port Klang, Malaysia"),("MYPKG","Port Klang"),
                ("Singapore","SGSIN"),("Laem Chabang, Thailand","THLCH")]:
        assert ports_match(normalize_port(a), normalize_port(b))[0] is True, (a,b)

def test_port_difference():
    assert ports_match(normalize_port("SHANGHAI"), normalize_port("NINGBO"))[0] is False

def test_port_undeterminable():
    assert ports_match(normalize_port("QQQQQ"), normalize_port(""))[0] is None

def test_count_parsing():
    for v,e in [("3",3),("03",3),("THREE",3),("3 x 40HC",3),("THREE (3)",3),
                ("4 CONTAINERS",4),(4,4)]:
        assert normalize_count(v)==e, v
    assert normalize_count("N/A") is None

def test_weight_parsing():
    assert normalize_weight_kg("22,000.00 KGS")==22000.0
    assert normalize_weight_kg("22 MT")==22000.0
    assert normalize_weight_kg("22.000,50 KG")==22000.5   # continental notation
    assert abs(normalize_weight_kg("48501.7 LBS")-22000)<1
    assert normalize_weight_kg("22000")==22000.0
    assert normalize_weight_kg("") is None

# ---------- comparator ----------
BASE_SI = {"shipper":"ABC CO., LTD.","consignee":"Meridian Logistics Pte. Ltd.",
           "notify_party":"Same as consignee","port_of_loading":"Port Kelang",
           "port_of_discharge":"Singapore","container_count":"3",
           "gross_weight_kg":"22,000.00 KGS"}
BASE_BL = {"shipper":"ABC Co Ltd","consignee":"MERIDIAN LOGISTICS PTE LTD",
           "notify_party":"SAME AS CONSIGNEE","port_of_loading":"MYPKG",
           "port_of_discharge":"SGSIN","container_count":"4","gross_weight_kg":"22 MT"}

def test_usecase_example():
    """From the use case: SI 3 containers 22000 kg, BL 4 containers 22000 kg -> flag only container count"""
    r = compare_documents("EM-1", BASE_SI, BASE_BL)
    assert r.has_mismatch and r.mismatched_fields == ["container_count"]
    assert "SI: 3 / BL: 4" in r.render()

def test_no_mismatch_wording():
    bl = dict(BASE_BL); bl["container_count"]="THREE (3)"
    r = compare_documents("EM-2", BASE_SI, bl)
    assert not r.has_mismatch
    assert "No mismatch detected." in r.render()   # exact wording required by the use case

def test_no_false_alarm_on_formatting():
    """All seven fields written differently but identical in content -> zero mismatches"""
    bl = dict(BASE_BL); bl["container_count"]="3"
    r = compare_documents("EM-3", BASE_SI, bl)
    assert r.mismatched_fields == []

def test_missing_field_escalates():
    bl = dict(BASE_BL); bl["port_of_discharge"]=None
    r = compare_documents("EM-4", BASE_SI, bl)
    assert r.needs_human_review
    assert any(x.field=="port_of_discharge" and x.outcome is Outcome.MISSING_BL
               for x in r.results)

def test_grey_zone_escalates_not_guesses():
    r = compare_field("shipper","ABC CO., LTD.","ABC INTERNATIONAL CO., LTD.")
    assert r.outcome in (Outcome.UNDETERMINED, Outcome.MISMATCH)

def test_determinism():
    a = compare_documents("EM-5", BASE_SI, BASE_BL).to_dict()
    b = compare_documents("EM-5", BASE_SI, BASE_BL).to_dict()
    assert a == b

def test_low_extract_confidence_routes_to_human():
    r = compare_documents("EM-6", BASE_SI, BASE_BL,
                          si_conf={"gross_weight_kg":0.31})
    assert r.needs_human_review

# ---------- evaluation ----------
def test_prf():
    m = prf(8,2,2); assert m.precision==0.8 and m.recall==0.8 and m.f1==0.8

def test_classification_report():
    y=["spam","doc","doc","invoice"]; p=["spam","doc","invoice","invoice"]
    rep=classification_report(y,p); assert rep["accuracy"]==0.75

def test_field_level_prf():
    g={"a":{"container_count"},"b":{"shipper","consignee"}}
    p={"a":{"container_count"},"b":{"shipper"},"c":{"notify_party"}}
    m=field_level_prf(g,p); assert (m.tp,m.fp,m.fn)==(2,1,1)

def test_calibration_perfect():
    conf=[0.95]*20; corr=[True]*19+[False]
    _,ece=calibration(conf,corr); assert ece<0.02

def test_calibration_overconfident():
    conf=[0.95]*20; corr=[True]*10+[False]*10
    _,ece=calibration(conf,corr); assert ece>0.4

def test_optimal_threshold_prefers_review_when_misses_costly():
    conf=[0.9]*10+[0.4]*10
    corr=[True]*10+[False]*10
    flg=[True]*10+[False]*10          # every low-confidence case is a missed defect
    best,curve=optimal_threshold(conf,corr,flg,cost_missed=8,cost_false_alarm=1,cost_review=0.35)
    assert 0.4 < best.threshold <= 0.9
    assert best.missed == 0
    assert len(curve)==41


# ---------- real-data regressions (every one of these bit us) ----------
def test_cjk_inline_label():
    """Real data: 'Gross Weight毛重(KGS):' occurs 51 times. CJK is isalnum()=True in Python"""
    assert resolve_label("Gross Weight毛重(KGS)") == ("gross_weight_kg", False)

def test_leading_qualifier_stripped():
    """PDFs say 'TOTAL Gross Wt (kgs):'"""
    assert resolve_label("TOTAL Gross Wt (kgs)") == ("gross_weight_kg", False)

def test_pdf_cjk_mojibake_fuzzy():
    """pdfplumber turns '毛重' into the garbage 'nn' - the fuzzy fallback must recover it"""
    assert resolve_label("TOTAL Gross Weightnn(KGS)") == ("gross_weight_kg", False)

def test_fuzzy_never_confuses_net_with_gross():
    """Negative case: NET WEIGHT must never fuzzy-match to a normal alias of GROSS WEIGHT"""
    key, near = resolve_label("NET WEIGHT")
    assert near is True           # must take the trap branch, never be usable as the field
    assert resolve_label("Vessel Name") == (None, False)
    assert resolve_label("Booking Ref") == (None, False)
    assert resolve_label("HS Code") == (None, False)


def test_wilson_interval_is_honest_on_small_n():
    from shipdoc_core.evaluate import wilson_interval, prf
    lo, hi = wilson_interval(15, 15)
    assert 0.79 < lo < 0.80 and hi == 1.0                     # 15/15 is not "1.0 +- 0"
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo2, hi2 = wilson_interval(150, 150)
    assert lo2 > lo                                            # more data, tighter interval
    d = prf(15, 0, 0).with_ci()
    assert d["precision"] == 1.0 and d["precision_ci95"][0] < 0.8 and d["recall_ci95"][1] == 1.0


def test_dotted_thousands_vs_tonne_decimals():
    from shipdoc_core.normalize import normalize_weight_kg
    assert normalize_weight_kg("22.000 KG") == 22000.0          # continental thousands in kilograms
    assert normalize_weight_kg("1.234.567 KG") == 1234567.0
    assert normalize_weight_kg("176.127 MT") == 176127.0        # tonnes with three decimals = kilograms
    assert normalize_weight_kg("22.5 MT") == 22500.0
    assert normalize_weight_kg("22,000.00 KGS") == 22000.0
