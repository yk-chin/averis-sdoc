"""
Field Ontology
==============
Pain point #3 from the use case: "One document may say 'Port of Loading' while the other says
'Load Port.' The system needs to recognize that these refer to the same field."

This file is that "recognize". It is deterministic, auditable and unit-testable -
not an LLM guessing each time which two labels mean the same thing.

Domain trap (something we know that other teams may not):
   Place of Receipt  !=  Port of Loading
   Place of Delivery !=  Port of Discharge
   The former are inland receipt/delivery points; the latter are the ports the vessel
   actually calls at. A naive alias table merges them and creates false alarms - and the
   use case explicitly demands "without creating false alarms". So they live in
   NEAR_MISS_LABELS: when hit they are NOT taken as the field; instead a
   "suspected label confusion" note is recorded for a human.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path


class FieldKind(str, Enum):
    PARTY = "party"          # company name + address, text
    PORT = "port"            # port name, possibly with UN/LOCODE
    COUNT = "count"          # integer
    WEIGHT_KG = "weight_kg"  # float, unit conversion required
    TEXT = "text"            # identifier-like text (B/L no., booking ref): compared on alphanumerics only


class Severity(str, Enum):
    HIGH = "high"       # directly affects customs clearance / delivery / billing


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: FieldKind
    severity: Severity
    aliases: tuple[str, ...]
    near_miss_labels: tuple[str, ...] = field(default=())


# The ontology is data, not code: shipdoc_core/fields.json (the seven fields named by the use case).
# Adding a field is one JSON entry - see load_ontology(); the comparator dispatches on `kind`.
ONTOLOGY_PATH = Path(__file__).with_name("fields.json")

FIELDS: list[FieldSpec] = []                 # mutated in place by load_ontology so every importer sees a reload
FIELD_BY_KEY: dict[str, FieldSpec] = {}
FIELD_KEYS: list[str] = []
_ALIAS_INDEX: dict[str, str] = {}
_NEAR_MISS_INDEX: dict[str, str] = {}


def _spec_from_json(d: dict) -> FieldSpec:
    try:
        return FieldSpec(key=d["key"], label=d["label"], kind=FieldKind(d["kind"]), severity=Severity(d.get("severity", "high")),
                         aliases=tuple(d["aliases"]), near_miss_labels=tuple(d.get("near_miss_labels", ())))
    except (KeyError, ValueError) as e:
        raise ValueError(f"ontology entry {d.get('key')!r}: {e}") from None


def load_ontology(path: str | Path | None = None) -> list[FieldSpec]:
    """(Re)load the field ontology from JSON. Validates keys are unique and kinds are known; rebuilds the
    alias / near-miss indexes in place. Called once at import; tests call it with another file to prove
    that an eighth field needs no Python change."""
    raw = json.loads(Path(path or ONTOLOGY_PATH).read_text(encoding="utf-8"))
    specs = [_spec_from_json(d) for d in raw["fields"]]
    keys = [f.key for f in specs]
    if len(set(keys)) != len(keys):
        raise ValueError(f"ontology: duplicate field keys {keys}")
    FIELDS[:] = specs
    FIELD_BY_KEY.clear(); FIELD_BY_KEY.update({f.key: f for f in specs})
    FIELD_KEYS[:] = keys
    _ALIAS_INDEX.clear(); _NEAR_MISS_INDEX.clear()
    for f in specs:
        for a in f.aliases:
            _ALIAS_INDEX[_norm_label(a)] = f.key
        for n in f.near_miss_labels:
            _NEAR_MISS_INDEX[_norm_label(n)] = f.key
    return specs



def _is_cjk(ch: str) -> bool:
    return "\u3000" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef"


def _norm_label(s: str) -> str:
    """Label normalisation. Real data mixes Chinese into labels ("Gross Weight毛重(KGS)"),
    and CJK characters are isalnum() == True in Python, so without explicit removal the match fails."""
    return " ".join(
        "".join(
            ch if (ch.isalnum() or ch.isspace()) and not _is_cjk(ch) else " "
            for ch in s.lower()
        ).split()
    )


load_ontology()



# Qualifiers that precede a field label without changing its meaning
_LEADING_QUALIFIERS = ("total", "grand total", "sub total", "subtotal",
                       "said to contain", "shipper declared", "declared")


def resolve_label(raw_label: str) -> tuple[str | None, bool]:
    """
    Map any label found in a document to one of the seven canonical fields.

    Returns (field_key, is_near_miss)
      - ("port_of_loading", False)  exact alias hit
      - ("port_of_loading", True)   near-miss trap label (Place of Receipt):
                                     must not be used as the field; hand to a human
      - (None, False)               unrecognised
    """
    key, near, _ = resolve_label_conf(raw_label)
    return key, near


def resolve_label_conf(raw_label: str) -> tuple[str | None, bool, float]:
    """resolve_label plus how sure the match is - the extraction confidence handed to the comparator:
    exact alias 1.0; alias after stripping a qualifier or bracket 0.95; fuzzy fallback = its ratio (>= 0.86)."""
    n = _norm_label(raw_label)
    if n in _ALIAS_INDEX:
        return _ALIAS_INDEX[n], False, 1.0
    if n in _NEAR_MISS_INDEX:
        return _NEAR_MISS_INDEX[n], True, 1.0

    # Fallback 1: strip leading qualifiers. Real PDFs say "TOTAL Gross Wt (kgs):";
    #             adding aliases one by one never ends, stripping qualifiers is more general.
    for q in _LEADING_QUALIFIERS:
        if n.startswith(q + " "):
            rest = n[len(q) + 1:].strip()
            if rest in _ALIAS_INDEX:
                return _ALIAS_INDEX[rest], False, 0.95
            if rest in _NEAR_MISS_INDEX:
                return _NEAR_MISS_INDEX[rest], True, 0.95
            n = rest
            break

    # Fallback 2: drop the parenthesised remark and try again
    stripped = n.split("(")[0].strip()
    if stripped and stripped in _ALIAS_INDEX:
        return _ALIAS_INDEX[stripped], False, 0.95
    if stripped and stripped in _NEAR_MISS_INDEX:
        return _NEAR_MISS_INDEX[stripped], True, 0.95

    # Fallback 3: fuzzy match. PDF extraction turns CJK into garbage
    #             ("Gross Weight毛重(KGS)" -> "Gross Weightnn(KGS)"), which no alias list can cover.
    #             Threshold 0.86; below it return None and hand to a human rather than guess -
    #             above all never map NET WEIGHT onto GROSS WEIGHT.
    return _fuzzy_resolve(n)


def _fuzzy_resolve(n: str, threshold: float = 0.86) -> tuple[str | None, bool, float]:
    best_key, best_near, best_ratio = None, False, 0.0
    for index, is_near in ((_NEAR_MISS_INDEX, True), (_ALIAS_INDEX, False)):
        for alias, key in index.items():
            r = SequenceMatcher(None, n, alias).ratio()
            if r > best_ratio:
                best_key, best_near, best_ratio = key, is_near, r
    return (best_key, best_near, round(best_ratio, 3)) if best_ratio >= threshold else (None, False, 0.0)
