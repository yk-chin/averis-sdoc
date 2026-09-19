"""normalize / parse_doc 的边界用例回归测试。每个用例都对应数据集里真实出现过的值对。"""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shipdoc_core.normalize import normalize_party, normalize_port, ports_match
from shipdoc_core.compare import compare_field, Outcome
from pipeline.parse_doc import parse_text_document, _deinterleave


# ---------------------------------------------------------------- 港口：港名优先于代码
def test_port_name_changed_but_code_kept_is_mismatch():
    # 主办方埋的缺陷形态：改港名、不改括号里的代码
    same, why = ports_match(normalize_port("FREMANTLE, AUSTRALIA (AUFRE)"),
                            normalize_port("BUSAN, SOUTH KOREA (AUFRE)"))
    assert same is False and "names differ" in why


def test_port_klang_westport_vs_singapore_same_code_is_mismatch():
    same, _ = ports_match(normalize_port("PORT KLANG (WESTPORT), MALAYSIA (MYPKG)"),
                          normalize_port("SINGAPORE, SINGAPORE (MYPKG)"))
    assert same is False


def test_country_word_is_not_taken_as_unlocode():
    # INDIA / KENYA / RUGAO / BEACH 都是 "合法国家码前缀 + 5 字母"，不能当代码
    for raw in ["NHAVA SHEVA, INDIA (INNSA)", "MOMBASA, KENYA (KEMBA)", "RUGAO/NANTONG/SHANGHAI, CHINA",
                "LONG BEACH, US"]:
        p = normalize_port(raw)
        assert p.unlocode not in ("INDIA", "KENYA", "RUGAO", "BEACH"), raw
    assert normalize_port("NHAVA SHEVA, INDIA (INNSA)").unlocode == "INNSA"


def test_port_format_variants_still_match():
    assert ports_match(normalize_port("PORT KLANG (WESTPORT), MALAYSIA"), normalize_port("PORT KLANG"))[0] is True
    assert ports_match(normalize_port("HOCHIMINH CITY, VIETNAM (VNSGN)"), normalize_port("HO CHI MINH CITY"))[0] is True
    assert ports_match(normalize_port("SINGAPORE, SINGAPORE (SGSIN)"), normalize_port("SGSIN"))[0] is True
    assert ports_match(normalize_port("NEW YORK, US (USNYC)"), normalize_port("NEW YORK, US"))[0] is True


def test_port_country_stripping_handles_unknown_countries():
    assert normalize_port("CONAKRY, GUINEA (GNCKY)").name == "CONAKRY"
    assert normalize_port("KLAIPEDA, LITHUANIA").name == "KLAIPEDA"
    assert normalize_port("SINGAPORE").name == "SINGAPORE"          # 城邦国家不可删空


def test_genuinely_different_ports_are_mismatch():
    assert ports_match(normalize_port("HOUSTON, US"), normalize_port("MOMBASA, KENYA"))[0] is False


# ---------------------------------------------------------------- 公司名：ON BEHALF OF
def test_on_behalf_of_agent_is_not_part_of_principal():
    si = "APRIL FINE PAPER TRADING | ON BEHALF OF VITAL SOLUTIONS PTE LTD; 77 ROBINSON ROAD, #21-01; SINGAPORE 068896"
    assert normalize_party(si) == normalize_party("APRIL FINE PAPER TRADING")
    r = compare_field("shipper", si, "APRIL FINE PAPER TRADING")
    assert r.outcome is Outcome.NORMALIZED_MATCH


def test_address_after_pipe_is_dropped():
    si = "KTP CO., LTD | KTP BLDG., 36 SANGWON-GIL; SEOUNGDONG-GU, SEOUL, SOUTH KOREA; TEL:02-2285-6025"
    assert normalize_party(si) == normalize_party("KTP CO., LTD")


def test_different_legal_entities_remain_mismatch():
    r = compare_field("shipper", "ASIA PACIFIC PAPERBOARD TRADING PTE LTD", "APRIL FAR EAST (M) SDN BHD")
    assert r.outcome is Outcome.MISMATCH


# ---------------------------------------------------------------- PDF 字符交错伪影
def test_deinterleave_recovers_value():
    assert _deinterleave("Party/Intermediate ConsCigEnReIEeX") == "CERIEX"
    assert _deinterleave("Party/Intermediate ConsKiTgPne CeO., LTD") == "KTP CO., LTD"
    assert _deinterleave("Party/Intermediate ConsNigAnGeAePPA EXPORTS") == "NAGAPPA EXPORTS"


def test_deinterleave_leaves_normal_values_alone():
    assert _deinterleave("CERIEX") == "CERIEX"
    assert _deinterleave("KTP CO., LTD") == "KTP CO., LTD"


def test_parse_doc_applies_deinterleave_to_notify_party():
    doc = parse_text_document("BILL OF LADING INSTRUCTION\nShipper: X\nCONSIGNEE: CERIEX\n"
                              "Notify: Party/Intermediate ConsCigEnReIEeX\nPOL: SINGAPORE\n")
    assert doc.fields["notify_party"] == "CERIEX"
