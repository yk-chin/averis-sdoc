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

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum


class FieldKind(str, Enum):
    PARTY = "party"          # company name + address, text
    PORT = "port"            # port name, possibly with UN/LOCODE
    COUNT = "count"          # integer
    WEIGHT_KG = "weight_kg"  # float, unit conversion required


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


# The seven fields named by the use case - no more, no fewer
FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="shipper",
        label="Shipper",
        kind=FieldKind.PARTY,
        severity=Severity.HIGH,
        aliases=(
            "shipper", "shipper/exporter", "shipper exporter", "exporter",
            "consignor", "shipper name", "shipper/consignor", "from",
            "shipper (principal or seller)", "seller", "shipper principal or seller",
        ),
    ),
    FieldSpec(
        key="consignee",
        label="Consignee",
        kind=FieldKind.PARTY,
        severity=Severity.HIGH,
        aliases=(
            "consignee", "consignee name", "consigned to", "consignee/receiver",
            "receiver", "to order of", "to the order of", "buyer",
            "consignee (non-negotiable)", "consignee non negotiable",
            "consignee (complete name and address)",
        ),
    ),
    FieldSpec(
        key="notify_party",
        label="Notify Party",
        kind=FieldKind.PARTY,
        severity=Severity.HIGH,
        aliases=(
            "notify party", "notify", "notify address", "notify party name",
            "notify party (if different)", "also notify", "notify applicant",
            "party to be notified", "notify party/intermediate consignee",
            "notify party intermediate consignee",
        ),
    ),
    FieldSpec(
        key="port_of_loading",
        label="Port of Loading",
        kind=FieldKind.PORT,
        severity=Severity.HIGH,
        aliases=(
            "port of loading", "load port", "pol", "loading port",
            "port of load", "port of shipment", "loading",
            "port of loading (pol)", "port of loading pol",
        ),
        near_miss_labels=("place of receipt", "place of acceptance", "pre-carriage from"),
    ),
    FieldSpec(
        key="port_of_discharge",
        label="Port of Discharge",
        kind=FieldKind.PORT,
        severity=Severity.HIGH,
        aliases=(
            "port of discharge", "discharge port", "pod", "port of unloading",
            "discharge", "port of destination", "discharging port",
            "port of discharge (pod)", "port of discharge pod",
        ),
        near_miss_labels=("place of delivery", "final destination", "on-carriage to"),
    ),
    FieldSpec(
        key="container_count",
        label="Container Count",
        kind=FieldKind.COUNT,
        severity=Severity.HIGH,
        aliases=(
            "container count", "number of containers", "no. of containers",
            "no of containers", "total containers", "qty of containers",
            "quantity of containers", "container qty", "containers",
            "no. of ctnrs", "total ctns",
            "no. of containers or packages", "no of containers or packages",
        ),
    ),
    FieldSpec(
        key="gross_weight_kg",
        label="Gross Weight (kg)",
        kind=FieldKind.WEIGHT_KG,
        severity=Severity.HIGH,
        aliases=(
            "gross weight", "gross weight (kgs)", "gross wt", "gross wt.",
            "g.w.", "gw", "total gross weight", "gross weight kg",
            "gross weight in kg", "total weight",
            "gross wt (kgs)", "gross wt kgs", "gross weight (kg)",
        ),
        # NET WEIGHT is never GROSS WEIGHT - both appear in the data; confusing them is a false alarm
        near_miss_labels=("net weight", "net wt", "nett weight", "n.w."),
    ),
)

FIELD_BY_KEY: dict[str, FieldSpec] = {f.key: f for f in FIELDS}
FIELD_KEYS: tuple[str, ...] = tuple(f.key for f in FIELDS)


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


_ALIAS_INDEX: dict[str, str] = {}
_NEAR_MISS_INDEX: dict[str, str] = {}
for _f in FIELDS:
    for _a in _f.aliases:
        _ALIAS_INDEX[_norm_label(_a)] = _f.key
    for _n in _f.near_miss_labels:
        _NEAR_MISS_INDEX[_norm_label(_n)] = _f.key


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
