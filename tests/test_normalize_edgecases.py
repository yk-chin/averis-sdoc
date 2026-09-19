"""Edge-case regressions for normalize / parse_doc. Every case corresponds to a value pair that really occurred in the dataset."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shipdoc_core.normalize import normalize_party, normalize_port, ports_match
from shipdoc_core.compare import compare_field, Outcome
from pipeline.parse_doc import parse_text_document, _deinterleave


# ---------------------------------------------------------------- ports: name over code
def test_port_name_changed_but_code_kept_is_mismatch():
    # the organiser's planted-defect shape: name changed, code in brackets untouched
    same, why = ports_match(normalize_port("FREMANTLE, AUSTRALIA (AUFRE)"),
                            normalize_port("BUSAN, SOUTH KOREA (AUFRE)"))
    assert same is False and "names differ" in why


def test_port_klang_westport_vs_singapore_same_code_is_mismatch():
    same, _ = ports_match(normalize_port("PORT KLANG (WESTPORT), MALAYSIA (MYPKG)"),
                          normalize_port("SINGAPORE, SINGAPORE (MYPKG)"))
    assert same is False


def test_country_word_is_not_taken_as_unlocode():
    # INDIA / KENYA / RUGAO / BEACH are all "valid country prefix + 5 letters" and must not count as codes
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
    assert normalize_port("SINGAPORE").name == "SINGAPORE"          # city-states must not be blanked


def test_genuinely_different_ports_are_mismatch():
    assert ports_match(normalize_port("HOUSTON, US"), normalize_port("MOMBASA, KENYA"))[0] is False


# ---------------------------------------------------------------- company names: ON BEHALF OF
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


# ---------------------------------------------------------------- PDF interleave artefact
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


# ---------------------------------------------------------------- R3: prefix relation beats the similarity score
def test_prefix_related_names_are_different_legal_entities():
    # email_145: similarity is exactly 0.750 - the verdict must not depend on that coincidence
    r = compare_field("shipper", "APRIL FINE PAPER TRADING", "APRIL FINE PAPER TRADING (MIDDLE EAST) FZE")
    assert r.outcome is Outcome.MISMATCH and "prefix" in r.reason
    r = compare_field("shipper", "APRIL FINE PAPER TRADING (MIDDLE EAST) FZE, DUBAI", "APRIL FINE PAPER TRADING")
    assert r.outcome is Outcome.MISMATCH


def test_prefix_rule_does_not_touch_same_entity_variants():
    # address after "|" and suffix spellings normalise to the same string -> still NORMALIZED_MATCH
    r = compare_field("shipper", "APRIL FINE PAPER TRADING (MIDDLE EAST) FZE | #813, 4 EA, DUBAI AIRPORT FREE ZONE",
                      "APRIL FINE PAPER TRADING (MIDDLE EAST) FZE")
    assert r.outcome is Outcome.NORMALIZED_MATCH
    r = compare_field("consignee", "Meridian Logistics Pte. Ltd.", "MERIDIAN LOGISTICS PTE LTD")
    assert r.outcome is Outcome.NORMALIZED_MATCH
