# ROI model (indicative; every input is labelled with its source)

Purpose: a structure Averis can fill in, not a claim. `scripts/roi.py` computes the table below from
`docs/roi_inputs.json` (standard library only); `tests/test_roi.py` pins the placeholder results.

## Correction (21 Sep)

The previous version of this model used a risk term `V × (m_h − m_s) × c_miss`, which counted every comparison
as if it contained a discrepancy. The corrected term multiplies by the prevalence `p_d` (the share of
comparisons with a real discrepancy). On the old central placeholders this overstated the risk saving about 5–7×.
A false-alarm cost term is added, because the README argues false alarms are the failure mode a BPO cannot absorb.

`m_s` is redefined as the **silent-miss rate**: the share of emails that contain a real discrepancy and are marked
OK by the system. It is 0 on the organiser's data and 0 on the hold-out (both hold-out false negatives were in
escalated emails, so a person saw them). The conservative scenario stresses it at 1 %.

## What Averis said (Workshop 2, 21 Sep 2026, verbal, indicative)

| Question | Answer as recorded | Used as |
|---|---|---|
| Q1 — checks per week and how many need a correction request | "Maybe around 20 till 30, if all correct no need to send to carrier" — the unit (per week? per day? checks or corrections?) was not established | **UNKNOWN**; `V` and `p_d` stay assumed |
| Q2 — cost of an error caught after B/L issue; cases per quarter | not answered | **UNKNOWN**; `N_esc`, `c_miss` stay assumed |
| Q3 — minutes per manual check; does a false alarm go straight to the carrier | "Around up to 10 mins for comparison, whole process maybe more"; routing not answered | `t_h` upper bound 10; `c_fa` stays assumed |

Nothing here is extrapolated: where the answer did not carry a number, the placeholder stays and is marked *assumed*.

## Formulas

```
L_man  = V * t_h/60 * c_h                      manual labour per day
L_sys  = V * c_sys                             system cost per day
L_esc  = V * r_esc * t_r/60 * c_h              reviewing escalations per day
L_fa   = V * r_fa * (t_fa/60 * c_h + c_fa)     dismissing false alarms per day (+ external cost if they reach the carrier)
S_lab  = (L_man - (L_sys + L_esc + L_fa)) * D  labour saving per year
E_man  = N_esc if provided else V * p_d * m_h * D
E_sys  = V * p_d * m_s * D
S_risk = max(0, E_man - E_sys) * c_miss
S_tot  = S_lab + S_risk
```

## Three scenarios

<!-- ROI:BEGIN -->
| | conservative | central | optimistic | source |
|---|---|---|---|---|
| `V` (comparisons per working day) | 80 | 150 | 250 | assumed (Averis Q1 answer 'maybe around 20 till 30' had no unit established) |
| `D` (working days per year) | 250 | 250 | 250 | assumed |
| `t_h` (minutes per manual check) | 6 | 8 | 10 | Averis, Workshop 2, 21 Sep 2026 (verbal, indicative): 'up to 10 minutes for the comparison, the whole process takes longer' - 10 taken as the upper bound, 6-8 assumed |
| `c_h` (RM per staff hour, fully loaded) | 30 | 35 | 40 | assumed |
| `p_d` (share of comparisons with a real discrepancy) | 0.1 | 0.15 | 0.209 | assumed; organiser data 46/220 = 0.209 |
| `m_h` (share of real discrepancies a manual check misses) | 0.02 | 0.05 | 0.08 | assumed (no cited source) |
| `N_esc` (errors found after B/L issue per year) | – | – | – | Averis Q2 - not answered |
| `m_s` (silent-miss rate: emails with a real discrepancy marked OK) | 0.01 | 0 | 0 | measured 0 on v2 and hold-out (both hold-out FNs were escalated); 1 % stress in the conservative case |
| `c_miss` (RM per escaped error, end to end) | 150 | 400 | 1000 | assumed (Averis Q2 not answered) |
| `r_esc` (share of comparisons escalated to a person) | 0.24 | 0.091 | 0.091 | measured: hold-out 6/25, v2 20/220 |
| `t_r` (minutes to review one escalation with the evidence panel) | 5 | 3 | 2 | assumed |
| `r_fa` (share of comparisons flagged as MISMATCH wrongly) | 0.2 | 0.05 | 0 | hold-out 5/25 (adversarial) / assumed / v2 0 |
| `t_fa` (minutes to dismiss one false alarm) | 5 | 5 | 3 | assumed |
| `c_fa` (RM external cost of a false alarm that reaches the carrier) | 50 | 0 | 0 | assumed (Averis Q3 routing not answered); 0 when a specialist confirms before anything is sent |
| `c_sys` (RM system cost per comparison) | 0.03 | 0.02 | 0.01 | measured: 55 % of emails make one flash-lite call; comparisons are free |
| **Manual labour / day** `L_man` | RM 240 | RM 700 | RM 1,667 | formula |
| System + escalations + false alarms / day | RM 890 | RM 49 | RM 33 | formula |
| **Labour saving / year** `S_lab` | −RM 162,600 | RM 162,809 | RM 408,458 | formula |
| Errors escaping manual / year `E_man` | 40.0 | 281.2 | 1,045.0 | formula or Averis N_esc |
| Errors escaping the system / year `E_sys` | 20.0 | 0.0 | 0.0 | formula |
| **Risk saving / year** `S_risk` | RM 3,000 | RM 112,500 | RM 1,045,000 | formula |
| **Total / year** `S_tot` | **−RM 159,600** | **RM 275,309** | **RM 1,453,458** | |
<!-- ROI:END -->

## How to read it

- **The conservative case is negative, and that is the point.** At the hold-out's false-alarm rate (5 of 25
  comparisons), *if false alarms reach the carrier* (`c_fa` > 0), the system destroys value. With a person confirming
  every MISMATCH before an amendment request goes out (`c_fa = 0`), the same case turns positive
  (`tests/test_roi.py::test_false_alarm_cost_flips_the_conservative_case`). Escalation precision is a business
  constraint, not a vanity metric — which is why the console has the Confirm / Correct step and why the ablation
  reports it.
- **The risk term dominates the central and optimistic cases**, and it rests on `p_d`, `m_h` and `c_miss` — all
  assumed. `m_h` (how often a manual check misses a real discrepancy) has no cited source; it stays an assumption
  until Averis provides `N_esc` (errors found after issue per year), which replaces the whole `V × p_d × m_h` estimate.
- The labour term is the smaller, better-founded part: `t_h` has an Averis upper bound, `r_esc` and `c_sys` are measured.

## To close the gaps

1. `V` — comparisons per working day (Q1 with the unit stated).
2. `N_esc` and `c_miss` — errors found after B/L issue per year and what one costs end to end (Q2).
3. Whether a flagged mismatch goes to the carrier unreviewed (`c_fa`) — the answer decides the conservative case (Q3).
4. `c_h` — the loaded hourly cost of the grade that does the check.
