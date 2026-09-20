# Indicative ROI model (every input is an assumption until Averis confirms it)

Purpose: a structure to fill in at Workshop 2 (21 Sep, Averis), not a claim. Numbers below are placeholders
marked *assumed*; the formulas are what we are proposing to agree on.

## Inputs

| Symbol | Meaning | Placeholder | Source |
|---|---|---|---|
| V | BL comparisons per working day | *assumed* 150 | ask Averis |
| t_h | minutes a specialist spends checking one SI vs draft BL by hand | *assumed* 8 (range 6–10) | ask Averis |
| c_h | fully loaded cost of a documentation specialist per hour (RM) | *assumed* 35 (RM 3,500–4,500 monthly salary, ×1.3 on-cost, 176 h) | ask Averis |
| r_esc | share of comparisons the system escalates to a person | measured 9 % on the organiser's data (20/220); hold-out 24 % (6/25) | this repo |
| t_r | minutes to review an escalation with the evidence panel | *assumed* 3 | measure in pilot |
| m_h | share of real differences a manual check misses | *assumed* 5 % | literature range for repetitive document checks; ask Averis |
| m_s | share the system misses | measured 0 % on v2 (defect recall 1.0, CI 0.80–1.00 on n = 15); hold-out defect F1 0.67 | this repo |
| c_miss | expected cost of one missed difference (amendment fee, discrepancy fee, delayed payment, roll-over) | *assumed* RM 400 (range 150–2,000) | ask Averis; UCP 600 discrepancy fees and carrier amendment fees are the floor |
| c_sys | system cost per comparison (Gemini tokens + Cloud Run + Firestore) | measured ≈ RM 0.01–0.03 (55 % of emails make one flash-lite call; comparisons are free) | this repo |

## Formulas

- Manual cost per day: `V × t_h / 60 × c_h`
- System cost per day: `V × c_sys + V × r_esc × t_r / 60 × c_h`
- Labour saving per day: manual − system
- Risk saving per day: `V × (m_h − m_s) × c_miss` — this term dominates whenever c_miss is real money
- Annual: × 250 working days

## Three scenarios (placeholders)

| | conservative | central | optimistic |
|---|---|---|---|
| V / day | 80 | 150 | 250 |
| t_h (min) | 6 | 8 | 10 |
| c_miss (RM) | 150 | 400 | 1,000 |
| Manual labour / day | RM 280 | RM 700 | RM 1,458 |
| System labour (escalations) / day | RM 34 | RM 63 | RM 105 |
| Labour saving / year | RM 61k | RM 159k | RM 338k |
| Risk saving / year (m_h 5 %, m_s 0 %) | RM 150k | RM 750k | RM 3.1M |

The risk term is larger than the labour term in every scenario — the value is fewer discrepancies reaching a bank or a carrier, not fewer minutes. That is also why escalation precision matters more than raw throughput: every false alarm spends `t_r` and, worse, trains people to ignore the queue.

## What to ask Averis at Workshop 2

1. Daily comparison volume and how it peaks around cut-offs.
2. Minutes per manual check today, and who does it (grade, cost).
3. What a missed difference has actually cost in the last year (amendment fees, LC discrepancy fees, roll-overs, claims).
4. Whether LC shipments should be prioritised in the queue (subjects already carry `LC` / `DP` / `CFR` / `OA`).
