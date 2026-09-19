"""
Value Normalization
===================
The use case demands real differences "without creating false alarms".

The biggest source of false alarms is not AI error - it is **formatting differences
mistaken for content differences**:
    "ABC CO., LTD."  vs  "ABC Co Ltd"          -> the same company
    "22,000.00 KGS"  vs  "22000 kg"            -> the same weight
    "22 MT"          vs  "22000 KG"            -> the same weight
    "PORT KELANG"    vs  "Port Klang, Malaysia" -> the same port
    "3"              vs  "THREE"  vs "03"      -> the same count

Every function is pure: same input, same output, unit-testable, explainable line by
line in a Q&A. No LLM calls anywhere.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------- common

def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def basic_clean(s: str) -> str:
    s = _strip_accents(str(s))
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[\r\n\t]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- company names

# Corporate-suffix equivalence classes. Keys are spellings, values the canonical form.
_CORP_SUFFIX = {
    "CO LTD": "CO LTD", "COLTD": "CO LTD", "CO LIMITED": "CO LTD",
    "COMPANY LIMITED": "CO LTD", "COMPANY LTD": "CO LTD",
    "LTD": "LTD", "LIMITED": "LTD",
    "INC": "INC", "INCORPORATED": "INC",
    "CORP": "CORP", "CORPORATION": "CORP",
    "LLC": "LLC", "L L C": "LLC",
    "PVT LTD": "PVT LTD", "PRIVATE LIMITED": "PVT LTD", "PVT LIMITED": "PVT LTD",
    "SDN BHD": "SDN BHD", "SENDIRIAN BERHAD": "SDN BHD",
    "BHD": "BHD", "BERHAD": "BHD",
    "PTE LTD": "PTE LTD", "PTE LIMITED": "PTE LTD",
    "GMBH": "GMBH", "AG": "AG", "NV": "NV", "BV": "BV", "SA": "SA",
    "PLC": "PLC", "PT": "PT", "KK": "KK",
}
# Longest first, so "COMPANY LIMITED" is not pre-empted by "LIMITED"
_CORP_KEYS = sorted(_CORP_SUFFIX, key=len, reverse=True)

# Address noise words to drop when comparing the legal entity
_ADDRESS_NOISE = re.compile(
    r"\b(STREET|ST|ROAD|RD|AVENUE|AVE|JALAN|JLN|LORONG|BLOCK|BLK|FLOOR|FLR|"
    r"LEVEL|LVL|SUITE|UNIT|NO|P\s*O\s*BOX|POBOX|TEL|FAX|EMAIL|ATTN)\b",
    re.I,
)


def _collapse_initials(s: str) -> str:
    """K.K. -> KK ; P.T. -> PT ; S.A. -> SA (abbreviation dots must not act as separators)"""
    return re.sub(r"\b(?:[A-Z]\.){2,}", lambda m: m.group(0).replace(".", ""), s)


# Agent / care-of qualifiers: what follows is the agent, not the legal entity itself.
#   In "APRIL FINE PAPER TRADING | ON BEHALF OF VITAL SOLUTIONS PTE LTD" the entity is APRIL;
#   without cutting first, the suffix search hits the agent's PTE LTD and takes the whole span -> false alarm.
_AGENT_QUALIFIER = re.compile(r"\b(ON\s+BEHALF\s+OF|O/B|C/O|CARE\s+OF|AS\s+AGENTS?\s+(?:FOR|OF))\b", re.I)


def normalize_party(raw: str, *, keep_address: bool = False) -> str:
    """
    Company-name normalisation. By default only the legal entity is kept (up to the
    corporate suffix), because address layouts differ wildly between SI and BL and
    comparing addresses is the number-one source of false alarms.

    Entity extraction (suffix-aware, better than a plain comma split):
      0. take the name line before "|" and cut the agent after ON BEHALF OF / C/O
      1. if a corporate suffix is found (LTD / SDN BHD / KK ...), entity = start -> end of suffix
      2. only if no suffix is found, fall back to the first segment by newline / comma
    keep_address=True keeps the full text, for showing the original in a review UI.
    """
    s = basic_clean(raw).upper()
    if not s:
        return ""
    s = _collapse_initials(s)

    suffix_hit = None
    if not keep_address:
        s = re.split(r"\s*\|\s*", s)[0]                 # name line; after "|" are address continuation lines
        m = _AGENT_QUALIFIER.search(s)
        if m and m.start() > 0:
            s = s[: m.start()]
        for key in _CORP_KEYS:
            pattern = r"\b" + r"[\s.,]*".join(map(re.escape, key.split())) + r"\b\.?"
            m = re.search(pattern, s)
            if m:
                suffix_hit = key
                s = s[: m.end()]
                break
        else:
            first = re.split(r"\s*\|\s*", s)[0]
            parts = re.split(r"\s*(?:,|;)\s*", first)
            s = parts[0] if len(parts[0]) >= 4 else first

    s = re.sub(r"[^\w\s&]", " ", s)
    s = _ADDRESS_NOISE.sub(" ", s)
    s = re.sub(r"\b\d{4,}\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    if suffix_hit:
        pattern = r"\b" + r"\s*".join(map(re.escape, suffix_hit.split())) + r"\b\s*$"
        s = re.sub(pattern, " " + _CORP_SUFFIX[suffix_hit], s)
    else:
        for key in _CORP_KEYS:
            pattern = r"\b" + r"\s*".join(map(re.escape, key.split())) + r"\b"
            if re.search(pattern, s):
                s = re.sub(pattern, " " + _CORP_SUFFIX[key] + " ", s)
                break
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- ports

_ISO2 = {
    "MY","SG","CN","IN","VN","TH","ID","JP","KR","US","NL","DE","GB","AU","TW",
    "HK","PH","BD","LK","PK","AE","SA","TR","IT","FR","ES","BE","PL","BR","MX",
    "ZA","EG","NZ","CA","RU","KH","MM","NP","QA","OM","KW","GR","PT","SE","NO",
    "DK","FI","IE","AT","CH","CZ","HU","RO","UA","IL","JO","LB","MA","NG","KE",
}
# Only accept a code in brackets "(MYPKG)", or a bare code that is in the known table.
# Never "any 5 capitals with a valid country prefix": INDIA(IN) / KENYA(KE) / RUGAO(RU) /
# BEACH(BE) all pass that check, country names become codes and the comparison is down to luck.
_UNLOCODE_PAREN_RE = re.compile(r"\(\s*([A-Z]{2}[A-Z0-9]{3})\s*\)")
_UNLOCODE_BARE_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{3})\b")


def _is_unlocode(tok: str) -> bool:
    """UN/LOCODE = ISO3166 two-letter country code + three-character place code.
    The country code must be validated, otherwise ordinary words like 'KLANG' 'PORTS' pass."""
    return len(tok) == 5 and tok[:2] in _ISO2

# Common misspellings / alternative port names. A real deployment would mount the full UN/LOCODE table; this is an extensible seed.
_PORT_SYNONYM = {
    "PORT KELANG": "PORT KLANG",
    "KELANG": "PORT KLANG",
    "KLANG": "PORT KLANG",
    "PELABUHAN KLANG": "PORT KLANG",
    "TANJUNG PELEPAS": "PORT OF TANJUNG PELEPAS",
    "PTP": "PORT OF TANJUNG PELEPAS",
    "HONGKONG": "HONG KONG",
    "HO CHI MINH": "HO CHI MINH CITY",
    "HOCHIMINH": "HO CHI MINH CITY",
    "HOCHIMINH CITY": "HO CHI MINH CITY",
    "SAIGON": "HO CHI MINH CITY",
    "BOMBAY": "MUMBAI",
    "MADRAS": "CHENNAI",
    "LOS ANGELES CA": "LOS ANGELES",
    "NEW YORK NY": "NEW YORK",
    "ROTTERDAM NL": "ROTTERDAM",
}

_COUNTRY_TAIL = re.compile(
    r"[,\s]+(MALAYSIA|SINGAPORE|CHINA|INDIA|VIETNAM|THAILAND|INDONESIA|JAPAN|"
    r"KOREA|USA|U\s*S\s*A|UNITED STATES|NETHERLANDS|GERMANY|UK|UNITED KINGDOM|"
    r"AUSTRALIA|TAIWAN|HONG KONG SAR)\s*$",
    re.I,
)


@dataclass(frozen=True)
class PortValue:
    name: str
    unlocode: str | None

    def key(self) -> str:
        # UN/LOCODE is the authoritative identifier; use it when present
        return self.unlocode or self.name


def normalize_port(raw: str) -> PortValue:
    s = basic_clean(raw).upper()
    if not s:
        return PortValue("", None)

    code = None
    m = _UNLOCODE_PAREN_RE.search(s)                 # preferred: the code in brackets
    if m and _is_unlocode(m.group(1)):
        code = m.group(1)
        s = s[: m.start()] + " " + s[m.end():]
    else:                                            # otherwise: a bare code that is in the known table
        for m in _UNLOCODE_BARE_RE.finditer(s):
            if m.group(1) in _LOCODE_NAME:
                code = m.group(1)
                s = s[: m.start()] + " " + s[m.end():]
                break

    s = re.sub(r"[()\[\]]", " ", s)
    # Strip the country: known country tail first; otherwise drop the last comma segment of "CITY, COUNTRY"
    stripped_tail = _COUNTRY_TAIL.sub("", s).strip()
    if stripped_tail and stripped_tail != s.strip():
        s = stripped_tail
    else:
        segs = [p.strip() for p in s.split(",") if p.strip()]
        if len(segs) >= 2:
            s = ", ".join(segs[:-1])
    if not s.strip() and code is None:   # city-states (SINGAPORE / HONG KONG) are the port name; never blank them
        s = basic_clean(raw).upper()     # (a bare code "MYPKG" legitimately leaves an empty name; no fallback)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _PORT_SYNONYM.get(s, s)
    return PortValue(s, code)


# ---------------------------------------------------------------- counts

_NUM_WORDS = {
    "ZERO": 0, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
    "ELEVEN": 11, "TWELVE": 12, "FIFTEEN": 15, "TWENTY": 20,
}


def normalize_count(raw) -> int | None:
    """
    Parse the container count. Supports '3' / '03' / 'THREE' / '3 x 40HC' / '3 CONTAINERS'
    / 'THREE (3)'. Returns None when unparseable (-> human review, never a guessed 0).
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)

    s = basic_clean(raw).upper()
    if not s:
        return None

    # 'THREE (3)': the Arabic numeral in brackets wins
    m = re.search(r"\((\d{1,4})\)", s)
    if m:
        return int(m.group(1))

    # '3 X 40HC' -> take the number before the multiplication sign
    m = re.match(r"^(\d{1,4})\s*[xX*]\s*\d", s)
    if m:
        return int(m.group(1))

    m = re.search(r"\b(\d{1,4})\b", s)
    if m:
        return int(m.group(1))

    for word, val in _NUM_WORDS.items():
        if re.search(r"\b" + word + r"\b", s):
            return val
    return None


# ---------------------------------------------------------------- weights

_UNIT_TO_KG = {
    "KG": 1.0, "KGS": 1.0, "KGM": 1.0, "KILOGRAM": 1.0, "KILOGRAMS": 1.0,
    "KILOS": 1.0, "K G S": 1.0,
    "MT": 1000.0, "TON": 1000.0, "TONS": 1000.0, "TONNE": 1000.0,
    "TONNES": 1000.0, "METRIC TON": 1000.0, "METRIC TONS": 1000.0, "T": 1000.0,
    "LB": 0.45359237, "LBS": 0.45359237, "POUND": 0.45359237, "POUNDS": 0.45359237,
    "G": 0.001, "GRAM": 0.001, "GRAMS": 0.001,
}
_UNIT_KEYS = sorted(_UNIT_TO_KG, key=len, reverse=True)


def _parse_number(tok: str) -> float | None:
    """
    Resolve the thousands-separator vs continental-decimal ambiguity:
        '22,000.00' -> 22000.00   (Anglo: comma thousands)
        '22.000,50' -> 22000.50   (continental: dot thousands)
        '22.000'    -> 22000      (three digits, no other decimal hint -> thousands)
    """
    t = tok.strip()
    if not t:
        return None
    has_c, has_d = "," in t, "." in t
    try:
        if has_c and has_d:
            return float(t.replace(".", "").replace(",", ".")) if t.rfind(",") > t.rfind(".") \
                else float(t.replace(",", ""))
        if has_c:
            # single comma followed by exactly 3 digits -> thousands; otherwise a decimal point
            return float(t.replace(",", "")) if re.fullmatch(r"\d{1,3}(,\d{3})+", t) \
                else float(t.replace(",", "."))
        if has_d:
            return float(t.replace(".", "")) if re.fullmatch(r"\d{1,3}(\.\d{3})+", t) \
                else float(t)
        return float(t)
    except ValueError:
        return None


def normalize_weight_kg(raw) -> float | None:
    """
    Parse the gross weight and convert to kilograms. Returns None when unparseable (-> human review).
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)

    s = basic_clean(raw).upper()
    if not s:
        return None

    m = re.search(r"(\d[\d.,\s]*\d|\d)", s)
    if not m:
        return None
    value = _parse_number(m.group(1).replace(" ", ""))
    if value is None:
        return None

    tail = s[m.end():].strip()
    for unit in _UNIT_KEYS:
        if re.match(r"^" + r"\s*".join(map(re.escape, unit.split())) + r"\b", tail):
            return round(value * _UNIT_TO_KG[unit], 4)
    for unit in _UNIT_KEYS:                       # unit written before the number
        if re.search(r"\b" + r"\s*".join(map(re.escape, unit.split())) + r"\b", s):
            return round(value * _UNIT_TO_KG[unit], 4)
    return round(value, 4)                        # no unit: kg by the use-case default


# LOCODE <-> port-name seed map, for matching when one side gives the code and the other the name.
# A real deployment mounts the full UN/LOCODE table; this covers the region's frequent ports and can grow.
_LOCODE_NAME = {
    "MYPKG": "PORT KLANG",      "MYTPP": "PORT OF TANJUNG PELEPAS",
    "MYPEN": "PENANG",          "MYJHB": "JOHOR BAHRU",
    "SGSIN": "SINGAPORE",       "HKHKG": "HONG KONG",
    "CNSHA": "SHANGHAI",        "CNNGB": "NINGBO",
    "CNSZX": "SHENZHEN",        "CNTAO": "QINGDAO",
    "INNSA": "NHAVA SHEVA",     "INMAA": "CHENNAI",
    "VNSGN": "HO CHI MINH CITY","THLCH": "LAEM CHABANG",
    "IDJKT": "JAKARTA",         "JPTYO": "TOKYO",
    "JPYOK": "YOKOHAMA",        "KRPUS": "BUSAN",
    "USLAX": "LOS ANGELES",     "USNYC": "NEW YORK",
    "NLRTM": "ROTTERDAM",       "DEHAM": "HAMBURG",
    "AEJEA": "JEBEL ALI",       "GBFXT": "FELIXSTOWE",
    # Added 2026-09-19: real UN/LOCODEs (industry data, NOT derived from the dataset; pairs like
    # AUFRE->BUSAN in the data are planted defects and must never be copied). IDBUA for Buatan
    # could not be confirmed as an official code and is left out.
    "CNNTG": "NANTONG",         "PECLL": "CALLAO",
    "PKKHI": "KARACHI",         "TRMER": "MERSIN",
    "KEMBA": "MOMBASA",         "AUFRE": "FREMANTLE",
    "GNCKY": "CONAKRY",         "LTKLJ": "KLAIPEDA",
    "NGAPP": "APAPA",           "USSAV": "SAVANNAH",
    "KRPTK": "PYEONGTAEK",      "MMRGN": "YANGON",
    "USBAL": "BALTIMORE",       "AUBNE": "BRISBANE",
    "USLGB": "LONG BEACH",      "ILASH": "ASHDOD",
    "PLGDN": "GDANSK",          "CLVAP": "VALPARAISO",
    "SIKOP": "KOPER",           "JOAQB": "AQABA",
    "USHOU": "HOUSTON",         "PHCEB": "CEBU",
    "INTUT": "TUTICORIN",
}
_NAME_LOCODE = {v: k for k, v in _LOCODE_NAME.items()}


def _port_names_equivalent(na: str, nb: str) -> bool:
    """Port names are equivalent when identical, or when one token set contains the other ("PORT KLANG WESTPORT" ⊇ "PORT KLANG")."""
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def ports_match(a: PortValue, b: PortValue) -> tuple[bool | None, str]:
    """
    Layered comparison of two port values.

    Returns (result, reason)
      True  same    False  different    None  undecidable -> must go to a human, never guess

    Port NAME takes precedence over the code. The real defects are "name changed, code in
    brackets left alone" (SI "FREMANTLE (AUFRE)" vs BL "BUSAN (AUFRE)") - comparing codes only
    would miss them. When both sides have names, names decide; same name but different code is
    also an internal inconsistency of the document -> different. The code is only used for
    cross-form matching when one side has no name ("SGSIN" vs "SINGAPORE").
    """
    if not (a.name or a.unlocode) or not (b.name or b.unlocode):
        return None, "one side empty"

    na = a.name or _LOCODE_NAME.get(a.unlocode or "", "")
    nb = b.name or _LOCODE_NAME.get(b.unlocode or "", "")
    ca = a.unlocode or _NAME_LOCODE.get(a.name)
    cb = b.unlocode or _NAME_LOCODE.get(b.name)

    if na and nb:
        if _port_names_equivalent(na, nb):
            if ca and cb and ca != cb:
                return False, f"name {na} same but unlocode {ca} vs {cb}"
            return True, f"name {na}"
        if ca and cb and ca == cb:
            return False, f"unlocode {ca} same but names differ: {na} vs {nb}"
        return False, f"name {na} vs {nb}"

    if ca and cb:
        return ca == cb, f"unlocode {ca} vs {cb}"

    return None, "unmapped locode, cannot compare"
