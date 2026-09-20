"""Property-style tests for the deterministic core (plain pytest + a seeded generator; no new dependency).
Each test states an invariant the comparator relies on and checks it over hundreds of generated inputs."""
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest

from shipdoc_core.compare import compare_documents, compare_field, Outcome, WEIGHT_REL_TOL, WEIGHT_ABS_TOL
from shipdoc_core.fields import FIELD_KEYS
from shipdoc_core.normalize import normalize_party, normalize_port, normalize_count, normalize_weight_kg, basic_clean

rng = random.Random(20260920)
N = 300

COMPANIES = ["Asia Pacific Paperboard Trading", "Roxcel Trading", "KPP Antalis", "East Bright", "Al Gurg Stationery",
             "April Fine Paper Trading", "Nagappa Exports", "International Forest Products", "Vital Solutions"]
SUFFIX_A = ["Pte Ltd", "Sdn Bhd", "Co., Ltd.", "LLC", "GmbH", "Limited", "Inc.", "FZE", "Pte. Ltd."]
SUFFIX_B = ["Pte. Limited", "Sendirian Berhad", "Company Limited", "L.L.C.", "GmbH", "Ltd", "Incorporated", "F.Z.E.", "PTE LTD"]
PORTS = ["Singapore", "Port Klang", "Nhava Sheva", "Karachi", "Gdansk", "Valparaiso", "Callao", "Tuticorin", "Nantong"]


def _party():
    return f"{rng.choice(COMPANIES)} {rng.choice(SUFFIX_A)}"


def _weight_variants(kg: float):
    """The same mass written in the notations seen in the data (and a few not seen)."""
    yield f"{kg:,.0f} KG"
    yield f"{kg:.0f} kgs"
    yield f"{kg / 1000:.3f} MT"
    yield f"{kg * 2.20462262:.1f} LBS"
    yield f"Gross weight {kg:,.2f} KGS"
    yield f"{kg / 1000:.2f} tons"


# ----------------------------------------------------------------------------- idempotence
def test_party_normalisation_is_idempotent():
    for _ in range(N):
        raw = _party() + rng.choice(["", " | 77 Robinson Road, Singapore", " ON BEHALF OF Someone Else Pte Ltd"])
        once = normalize_party(raw)
        assert normalize_party(once) == once


def test_port_normalisation_is_idempotent():
    for _ in range(N):
        raw = rng.choice(PORTS) + rng.choice(["", ", MALAYSIA", " (SGSIN)", " (MYPKG)"])
        once = normalize_port(raw)
        assert normalize_port(once.name).name == once.name


def test_basic_clean_is_idempotent():
    for _ in range(N):
        raw = "  " + rng.choice(COMPANIES) + "　" + rng.choice(["  ", "\t", "\n"]) + rng.choice(PORTS)
        assert basic_clean(basic_clean(raw)) == basic_clean(raw)


# ----------------------------------------------------------------------------- unit round trips
def test_weight_round_trips_across_units_within_policy_tolerance():
    for _ in range(N):
        kg = round(rng.uniform(500, 300_000), 0)
        for text in _weight_variants(kg):
            got = normalize_weight_kg(text)
            assert got is not None, text
            tol = max(WEIGHT_ABS_TOL, kg * WEIGHT_REL_TOL)
            assert abs(got - kg) <= tol, (text, got, kg)


def test_count_notations_agree():
    for _ in range(N):
        n = rng.randint(1, 40)
        size = rng.choice(["20'FCL", "40'HC", "40'GP", "20GP"])
        variants = [str(n), f"{n} x {size}", f"{n}x{size}", f"{n} containers", f"{n} X {size}"]
        got = {normalize_count(v) for v in variants}
        assert got == {n}, (variants, got)


# ----------------------------------------------------------------------------- symmetry
def test_comparison_outcome_is_symmetric_in_si_and_bl():
    """Swapping the two documents never changes the verdict (only which side is called SI)."""
    for _ in range(N):
        si = {"shipper": _party(), "consignee": _party(), "notify_party": _party(),
              "port_of_loading": rng.choice(PORTS), "port_of_discharge": rng.choice(PORTS),
              "container_count": str(rng.randint(1, 20)), "gross_weight_kg": f"{rng.randint(1000, 90000)} KG"}
        bl = dict(si)
        for k in rng.sample(FIELD_KEYS, rng.randint(0, 3)):           # perturb a few fields
            if k == "container_count":
                bl[k] = str(int(bl[k]) + rng.choice([0, 1]))
            elif k == "gross_weight_kg":
                bl[k] = f"{float(bl[k].split()[0]) / 1000:.3f} MT"
            elif k.startswith("port"):
                bl[k] = rng.choice(PORTS)
            else:
                base = bl[k].rsplit(" ", 1)[0] if bl[k].split()[-1] in ("Ltd", "LLC", "FZE") else bl[k]
                bl[k] = base + " " + rng.choice(SUFFIX_B)
        a = compare_documents("x", si, bl)
        b = compare_documents("x", bl, si)
        assert sorted(a.mismatched_fields) == sorted(b.mismatched_fields)
        assert a.has_mismatch == b.has_mismatch and a.needs_human_review == b.needs_human_review
        for ra, rb in zip(a.results, b.results):
            assert ra.outcome == rb.outcome, (ra, rb)


# ----------------------------------------------------------------------------- formatting is never a defect
def test_suffix_spelling_and_punctuation_never_produce_a_mismatch():
    for _ in range(N):
        base = rng.choice(COMPANIES)
        i = rng.randrange(len(SUFFIX_A))
        a, b = f"{base} {SUFFIX_A[i]}", f"{base.upper()} {SUFFIX_B[i]}"
        r = compare_field("shipper", a, b)
        assert r.outcome in (Outcome.EXACT, Outcome.NORMALIZED_MATCH), (a, b, r.reason)


def test_same_weight_in_any_unit_is_a_match_and_a_real_difference_is_not():
    for _ in range(N):
        kg = float(rng.randint(2000, 200_000))
        variants = list(_weight_variants(kg))
        r = compare_field("gross_weight_kg", rng.choice(variants), rng.choice(variants))
        assert r.outcome in (Outcome.EXACT, Outcome.NORMALIZED_MATCH), r.reason
        other = kg * rng.choice([1.05, 0.9, 2.0])
        r2 = compare_field("gross_weight_kg", f"{kg:,.0f} KG", f"{other:,.0f} KG")
        assert r2.outcome is Outcome.MISMATCH, r2.reason


@pytest.mark.parametrize("bad", ["", "   ", "N/A", "n.a.", "TBA", "TBD", "-", "____", "???", "pending"])
def test_blank_or_placeholder_values_are_reported_missing(bad):
    """A placeholder is an absent value, never a 95 %-confident mismatch against a real name."""
    r = compare_field("consignee", bad, "XYZ LLC")
    assert r.outcome is Outcome.MISSING_SI and r.needs_review
    r = compare_field("gross_weight_kg", "22000 KG", bad)
    assert r.outcome is Outcome.MISSING_BL and r.needs_review
