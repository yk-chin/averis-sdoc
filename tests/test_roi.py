"""The ROI script must reproduce the acceptance numbers for the placeholder inputs (docs/ROI.md)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from scripts.roi import compute, load


def test_placeholder_scenarios_reproduce_acceptance_numbers():
    scen, per = load()
    res = {s: compute(per[s]) for s in scen}
    assert abs(res["central"]["S_tot"] - 275_309) <= 1
    assert abs(res["central"]["S_risk"] - 112_500) <= 1
    assert abs(res["conservative"]["S_tot"] - (-159_600)) <= 1
    assert abs(res["optimistic"]["S_tot"] - 1_453_458) <= 1


def test_false_alarm_cost_flips_the_conservative_case():
    """With a specialist confirming every MISMATCH before anything is sent (c_fa = 0) the conservative case turns positive."""
    _, per = load()
    x = dict(per["conservative"]); x["c_fa"] = 0
    assert compute(x)["S_tot"] > 0


def test_n_esc_overrides_the_prevalence_estimate():
    _, per = load()
    x = dict(per["central"]); x["N_esc"] = 12
    r = compute(x)
    assert r["E_man"] == 12 and r["S_risk"] == 12 * x["c_miss"]
