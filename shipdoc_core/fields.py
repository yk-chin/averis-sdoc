"""
字段本体 (Field Ontology)
=========================
用例痛点三原文：「One document may say "Port of Loading" while the other says
"Load Port." The system needs to recognize that these refer to the same field.」

这个文件就是那个"recognize"。它是确定性的、可审计的、可单测的——
不是让 LLM 每次自己猜哪两个标签是同一个意思。

⚠️ 领域陷阱（这是我们比其他队多知道的东西）：
   Place of Receipt  ≠  Port of Loading
   Place of Delivery ≠  Port of Discharge
   前者是内陆收/交货点，后者是船舶实际靠泊港。
   天真的别名表会把它们混为一谈，制造假警报 —— 而用例明确要求
   "without creating false alarms"。所以它们被放进 NEAR_MISS_LABELS，
   命中时不当作该字段，而是记一条"疑似标签混淆"的证据交人工。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum


class FieldKind(str, Enum):
    PARTY = "party"          # 公司名 + 地址，文本型
    PORT = "port"            # 港口名，可能带 UN/LOCODE
    COUNT = "count"          # 整数
    WEIGHT_KG = "weight_kg"  # 浮点，单位需换算


class Severity(str, Enum):
    HIGH = "high"       # 直接影响清关 / 交付 / 计费
    MEDIUM = "medium"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: FieldKind
    severity: Severity
    aliases: tuple[str, ...]
    near_miss_labels: tuple[str, ...] = field(default=())


# 用例指定的七个字段，一个不多一个不少
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
        # ⚠️ NET WEIGHT 绝不是 GROSS WEIGHT —— 数据里两者同时出现，混淆即假警报
        near_miss_labels=("net weight", "net wt", "nett weight", "n.w."),
    ),
)

FIELD_BY_KEY: dict[str, FieldSpec] = {f.key: f for f in FIELDS}
FIELD_KEYS: tuple[str, ...] = tuple(f.key for f in FIELDS)


def _is_cjk(ch: str) -> bool:
    return "\u3000" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef"


def _norm_label(s: str) -> str:
    """标签归一。⚠️ 真实数据里标签混排中文（"Gross Weight毛重(KGS)"），
    而 CJK 在 Python 里 isalnum() 为 True，不显式剔除就会匹配失败。"""
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


# 出现在字段标签前的限定词，本身不改变字段含义
_LEADING_QUALIFIERS = ("total", "grand total", "sub total", "subtotal",
                       "said to contain", "shipper declared", "declared")


def resolve_label(raw_label: str) -> tuple[str | None, bool]:
    """
    把文档里出现的任意标签映射到七个规范字段之一。

    返回 (field_key, is_near_miss)
      - ("port_of_loading", False)  精确别名命中
      - ("port_of_loading", True)   命中近义陷阱标签（Place of Receipt），
                                     不可当作该字段使用，应交人工确认
      - (None, False)               无法识别
    """
    n = _norm_label(raw_label)
    if n in _ALIAS_INDEX:
        return _ALIAS_INDEX[n], False
    if n in _NEAR_MISS_INDEX:
        return _NEAR_MISS_INDEX[n], True

    # 退化 1：剥离前置限定词。真实 PDF 里是 "TOTAL Gross Wt (kgs):"，
    #         逐个补别名会补不完，剥离限定词更通用。
    for q in _LEADING_QUALIFIERS:
        if n.startswith(q + " "):
            rest = n[len(q) + 1:].strip()
            if rest in _ALIAS_INDEX:
                return _ALIAS_INDEX[rest], False
            if rest in _NEAR_MISS_INDEX:
                return _NEAR_MISS_INDEX[rest], True
            n = rest
            break

    # 退化 2：去掉括号补充说明后再试一次
    stripped = n.split("(")[0].strip()
    if stripped and stripped in _ALIAS_INDEX:
        return _ALIAS_INDEX[stripped], False
    if stripped and stripped in _NEAR_MISS_INDEX:
        return _NEAR_MISS_INDEX[stripped], True

    # 退化 3：模糊兜底。PDF 抽取会把 CJK 抽成乱码
    #         （"Gross Weight毛重(KGS)" → "Gross Weightnn(KGS)"），
    #         逐个补别名补不完。阈值取 0.86，低于它宁可返回 None 交人工，
    #         绝不猜 —— 尤其不能把 NET WEIGHT 错配成 GROSS WEIGHT。
    return _fuzzy_resolve(n)


def _fuzzy_resolve(n: str, threshold: float = 0.86) -> tuple[str | None, bool]:
    best_key, best_near, best_ratio = None, False, 0.0
    for index, is_near in ((_NEAR_MISS_INDEX, True), (_ALIAS_INDEX, False)):
        for alias, key in index.items():
            r = SequenceMatcher(None, n, alias).ratio()
            if r > best_ratio:
                best_key, best_near, best_ratio = key, is_near, r
    return (best_key, best_near) if best_ratio >= threshold else (None, False)
