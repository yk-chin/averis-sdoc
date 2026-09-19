"""
值规范化 (Value Normalization)
==============================
用例要求：找出真实差异，且 "without creating false alarms"。

假警报的最大来源不是 AI 出错，是**格式差异被当成了内容差异**：
    "ABC CO., LTD."  vs  "ABC Co Ltd"          → 同一家公司
    "22,000.00 KGS"  vs  "22000 kg"            → 同一个重量
    "22 MT"          vs  "22000 KG"            → 同一个重量
    "PORT KELANG"    vs  "Port Klang, Malaysia" → 同一个港口
    "3"              vs  "THREE"  vs "03"      → 同一个数量

每个函数都是纯函数：同样输入永远同样输出，可以写单测，可以在 Q&A 里
逐行解释。全程零 LLM 调用。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------- 通用

def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def basic_clean(s: str) -> str:
    s = _strip_accents(str(s))
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[\r\n\t]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- 公司名

# 公司后缀等价类。键是各种写法，值是规范形式。
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
# 长的先匹配，避免 "COMPANY LIMITED" 被 "LIMITED" 抢先
_CORP_KEYS = sorted(_CORP_SUFFIX, key=len, reverse=True)

# 地址里常见的噪声词，比对公司主体时应剔除
_ADDRESS_NOISE = re.compile(
    r"\b(STREET|ST|ROAD|RD|AVENUE|AVE|JALAN|JLN|LORONG|BLOCK|BLK|FLOOR|FLR|"
    r"LEVEL|LVL|SUITE|UNIT|NO|P\s*O\s*BOX|POBOX|TEL|FAX|EMAIL|ATTN)\b",
    re.I,
)


def _collapse_initials(s: str) -> str:
    """K.K. -> KK ; P.T. -> PT ; S.A. -> SA（缩写点不应被当成分词）"""
    return re.sub(r"\b(?:[A-Z]\.){2,}", lambda m: m.group(0).replace(".", ""), s)


# 代理/转交限定语：其后是代理方而非法人主体本身。
#   "APRIL FINE PAPER TRADING | ON BEHALF OF VITAL SOLUTIONS PTE LTD" 的主体是 APRIL，
#   若不先切掉，后缀搜索会命中代理方的 PTE LTD，把整段当成主体 → 假警报。
_AGENT_QUALIFIER = re.compile(r"\b(ON\s+BEHALF\s+OF|O/B|C/O|CARE\s+OF|AS\s+AGENTS?\s+(?:FOR|OF))\b", re.I)


def normalize_party(raw: str, *, keep_address: bool = False) -> str:
    """
    公司名规范化。默认只保留法人主体（截到公司后缀为止），因为 SI 与 BL 的
    地址排版差异极大，把地址纳入比对是假警报的头号来源。

    主体提取策略（后缀感知，优于简单按逗号切）：
      0. 先取 "|" 之前的名称行，并切掉 ON BEHALF OF / C/O 之后的代理方
      1. 若找到公司后缀（LTD / SDN BHD / KK ...），主体 = 开头 → 后缀结束
      2. 找不到后缀，才退回按换行 / 逗号取第一段
    keep_address=True 时保留全文，用于人工复核界面展示原文。
    """
    s = basic_clean(raw).upper()
    if not s:
        return ""
    s = _collapse_initials(s)

    suffix_hit = None
    if not keep_address:
        s = re.split(r"\s*\|\s*", s)[0]                 # 名称行；"|" 之后是地址续行
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


# ---------------------------------------------------------------- 港口

_ISO2 = {
    "MY","SG","CN","IN","VN","TH","ID","JP","KR","US","NL","DE","GB","AU","TW",
    "HK","PH","BD","LK","PK","AE","SA","TR","IT","FR","ES","BE","PL","BR","MX",
    "ZA","EG","NZ","CA","RU","KH","MM","NP","QA","OM","KW","GR","PT","SE","NO",
    "DK","FI","IE","AT","CH","CZ","HU","RO","UA","IL","JO","LB","MA","NG","KE",
}
# 只认括号里的代码 "(MYPKG)"，或裸露但在已知表里的代码。
# ⚠️ 不能用"任意 5 个大写字母 + 合法国家码前缀"：INDIA(IN) / KENYA(KE) / RUGAO(RU) /
#    BEACH(BE) 都会通过校验，把国名当成代码 → 比对结果靠运气。
_UNLOCODE_PAREN_RE = re.compile(r"\(\s*([A-Z]{2}[A-Z0-9]{3})\s*\)")
_UNLOCODE_BARE_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{3})\b")


def _is_unlocode(tok: str) -> bool:
    """UN/LOCODE = ISO3166 两位国家码 + 三位地名码。
    必须校验国家码，否则 'KLANG' 'PORTS' 这类普通词会被误判。"""
    return len(tok) == 5 and tok[:2] in _ISO2

# 常见异拼 / 同港异名。真实项目应挂 UN/LOCODE 全表，这里是可扩展的种子。
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
        # UN/LOCODE 是权威标识，有就用它
        return self.unlocode or self.name


def normalize_port(raw: str) -> PortValue:
    s = basic_clean(raw).upper()
    if not s:
        return PortValue("", None)

    code = None
    m = _UNLOCODE_PAREN_RE.search(s)                 # 首选：括号里的代码
    if m and _is_unlocode(m.group(1)):
        code = m.group(1)
        s = s[: m.start()] + " " + s[m.end():]
    else:                                            # 其次：裸露但在已知表里
        for m in _UNLOCODE_BARE_RE.finditer(s):
            if m.group(1) in _LOCODE_NAME:
                code = m.group(1)
                s = s[: m.start()] + " " + s[m.end():]
                break

    s = re.sub(r"[()\[\]]", " ", s)
    # 去国家：优先按已知国名尾巴；否则 "CITY, COUNTRY" 形式去掉最后一个逗号段
    stripped_tail = _COUNTRY_TAIL.sub("", s).strip()
    if stripped_tail and stripped_tail != s.strip():
        s = stripped_tail
    else:
        segs = [p.strip() for p in s.split(",") if p.strip()]
        if len(segs) >= 2:
            s = ", ".join(segs[:-1])
    if not s.strip() and code is None:   # 城邦国家(SINGAPORE/HONG KONG)本身即港名，不可删空
        s = basic_clean(raw).upper()     # （纯代码 "MYPKG" 抽走后名字为空是正常的，不兜底）
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _PORT_SYNONYM.get(s, s)
    return PortValue(s, code)


# ---------------------------------------------------------------- 数量

_NUM_WORDS = {
    "ZERO": 0, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
    "ELEVEN": 11, "TWELVE": 12, "FIFTEEN": 15, "TWENTY": 20,
}


def normalize_count(raw) -> int | None:
    """
    解析集装箱数量。支持：'3' / '03' / 'THREE' / '3 x 40HC' / '3 CONTAINERS'
    / 'THREE (3)' 。无法解析返回 None（→ 触发人工复核，而不是猜 0）。
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)

    s = basic_clean(raw).upper()
    if not s:
        return None

    # 'THREE (3)' 这种括号里的阿拉伯数字优先
    m = re.search(r"\((\d{1,4})\)", s)
    if m:
        return int(m.group(1))

    # '3 X 40HC' -> 取乘号前的数
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


# ---------------------------------------------------------------- 重量

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
    处理千分位与欧陆小数点的歧义：
        '22,000.00' → 22000.00   （英美：逗号千分位）
        '22.000,50' → 22000.50   （欧陆：点千分位）
        '22.000'    → 22000      （三位且无其他小数线索 → 千分位）
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
            # 单逗号且后跟恰好 3 位 → 千分位；否则视作小数点
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
    解析毛重并统一换算成公斤。无法解析返回 None（→ 人工复核）。
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
    for unit in _UNIT_KEYS:                       # 单位写在数字前的情况
        if re.search(r"\b" + r"\s*".join(map(re.escape, unit.split())) + r"\b", s):
            return round(value * _UNIT_TO_KG[unit], 4)
    return round(value, 4)                        # 无单位：按用例默认 kg


# LOCODE ↔ 港名 种子映射，用于一边给代码、一边给名字时的跨形态匹配。
# 真实项目挂 UN/LOCODE 全表；这里覆盖本区域高频港口，可随数据集扩充。
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
    # 2026-09-19 扩充：真实 UN/LOCODE（行业数据，非从数据集反推；数据集里 AUFRE→BUSAN 之类
    # 的"错配"是主办方埋的缺陷，绝不能照抄进来）。Buatan 的 IDBUA 未能确认为官方代码，未收录。
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
    """港名等价：完全相同，或一方的词集包含另一方（"PORT KLANG WESTPORT" ⊇ "PORT KLANG"）。"""
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def ports_match(a: PortValue, b: PortValue) -> tuple[bool | None, str]:
    """
    分层比对两个港口值。

    返回 (结果, 理由)
      True  相同    False 不同    None 无法判定 → 必须交人工，不许猜

    ⚠️ 港名优先于代码。真实缺陷是"港名改了、括号里的代码没改"
       （SI "FREMANTLE (AUFRE)" vs BL "BUSAN (AUFRE)"）——若只比代码会漏掉。
       两边都有名字时以名字为准；名字一致但代码不一致，同样是单据内部矛盾 → 不同。
       代码只在一方没有名字时用来跨形态匹配（"SGSIN" vs "SINGAPORE"）。
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
