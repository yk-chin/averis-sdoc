"""The field ontology is configuration: an eighth field needs a JSON entry, no Python."""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from shipdoc_core import fields as F
from shipdoc_core.compare import compare_documents
from pipeline.parse_doc import parse_text_document


@pytest.fixture
def restore_ontology():
    yield
    F.load_ontology()                                   # back to the shipped seven fields


def test_shipped_ontology_is_the_seven_fields():
    assert tuple(F.FIELD_KEYS) == ("shipper", "consignee", "notify_party", "port_of_loading",
                                   "port_of_discharge", "container_count", "gross_weight_kg")
    raw = json.loads(F.ONTOLOGY_PATH.read_text(encoding="utf-8"))
    assert [d["key"] for d in raw["fields"]] == list(F.FIELD_KEYS)


def test_eighth_field_from_config_only(tmp_path, restore_ontology):
    raw = json.loads(F.ONTOLOGY_PATH.read_text(encoding="utf-8"))
    raw["fields"].append({"key": "bl_number", "label": "B/L Number", "kind": "text", "severity": "high",
                          "aliases": ["b/l number", "bl number", "bill of lading no", "b/l no.", "bl no"]})
    p = tmp_path / "fields.json"; p.write_text(json.dumps(raw), encoding="utf-8")
    F.load_ontology(p)
    assert F.FIELD_KEYS[-1] == "bl_number" and F.resolve_label("B/L No.") == ("bl_number", False)

    si = parse_text_document("SHIPPING INSTRUCTION\nShipper: ABC CO LTD\nConsignee: XYZ LLC\nNotify: XYZ LLC\n"
                             "POL: SINGAPORE\nPOD: PORT KLANG\nContainer Count: 3\nGross Weight: 22000 KG\nB/L Number: MAEU 123-456\n")
    bl = parse_text_document("BILL OF LADING (DRAFT)\nShipper: ABC CO LTD\nConsignee: XYZ LLC\nNotify: XYZ LLC\n"
                             "Port of Loading: SINGAPORE\nPort of Discharge: PORT KLANG\nTotal Containers: 3\nGross Wt (kgs): 22,000\nBL No: maeu123456\n")
    assert si.fields["bl_number"] == "MAEU 123-456"
    rep = compare_documents("x", si.fields, bl.fields)
    r = {x.field: x for x in rep.results}["bl_number"]
    assert r.outcome.value == "normalized" and not rep.has_mismatch        # spacing/case/punctuation only

    bl2 = dict(bl.fields, bl_number="MAEU 999")
    rep2 = compare_documents("x", si.fields, bl2)
    assert "bl_number" in rep2.mismatched_fields


def test_bad_ontology_is_rejected(tmp_path, restore_ontology):
    raw = json.loads(F.ONTOLOGY_PATH.read_text(encoding="utf-8"))
    raw["fields"].append({"key": "shipper", "label": "dup", "kind": "party", "aliases": ["x"]})
    p = tmp_path / "dup.json"; p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        F.load_ontology(p)
    raw["fields"][-1] = {"key": "odd", "label": "odd", "kind": "hologram", "aliases": ["x"]}
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        F.load_ontology(p)
