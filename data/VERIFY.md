# ⚠ VERIFY values in use (owner: Tudor)

Every placeholder the sim, pilot and rules use. Confirm or correct each one before the demo; the file and constant to change are listed.

| # | Item | Value used now | Where |
|---|---|---|---|
| 1 | Minimum altitude, general | 3,000 ft | `server/sim/airspace.py` |
| 2 | Minimum altitude, final approach area | 2,000 ft within 15 NM of the threshold, ±3 NM of the centreline | `server/sim/airspace.py` |
| 3 | Transition altitude | 6,000 ft (altitude 6,000 itself valid) | `server/sim/world.py` `TRANSITION_ALT_FT` |
| 4 | Transition level | FL70 at QNH ≥ 1013, FL75 at 978–1012, FL80 below | `server/sim/airspace.py` |
| 5 | Wake minima on final | H>H 4, H>M 5, H>L 6, M>L 5, else 3 NM (ICAO) | `data/traffic.json` `final_approach_wake_nm` |
| 6 | Departure wake / spacing | wake 2 NM; same route 2 NM; diverging ≥45° 1 NM | `data/traffic.json` |
| 7 | Departure release gap | arrival must be ≥ 6 NM from touchdown | `data/traffic.json` `departure_release_gap_nm` |
| 8 | Runway occupancy | arrival 55 s, departure 45 s | `data/traffic.json` `runway_occupancy_s` |
| 9 | Final approach speeds | 180 kt to 8 NM, 160 kt to 4 NM, then Vapp | `server/sim/modes.py` `FINAL_SPEEDS` |
| 10 | Localiser capture | intercept ≤ 45°, cross-track < 2 NM | `server/sim/modes.py` |
| 11 | ILS intercept rule (B4) | warn > 30°, fail > 45°, intercept ≥ 2 NM before FI26L | `server/rules/rules_instruction.py` |
| 12 | FI26L position | where the 3° glidepath meets 2,000 ft (guess) | `server/rules/rules_instruction.py` `FAF_ALT_FT` |
| 13 | Missed approach 26L/08R | runway track, 3,000 ft, 180 kt | `server/sim/modes.py` |
| 14 | Holds (WILLO, TIMBA) | right turns, 1-min legs, inbound = arrival track, max 220 kt | `server/sim/modes.py` |
| 15 | SID initial altitude | 6,000 ft | `server/sim/runway.py` `SID_INITIAL_ALT_FT` |
| 16 | Takeoff | 3 kt/s to Vr = Vapp + 10; ~V2+10 to 1,500 ft; 220 kt to 3,000 ft; then 250 kt | `server/sim/runway.py` |
| 17 | Departure check-in | passing 2,000 ft | `server/sim/runway.py` |
| 18 | Departure "request higher" | level at 6,000 ft for > 60 s | `server/sim/runway.py` |
| 19 | Departure not transferred (B10) | end of SID or 25 NM | `server/sim/runway.py` |
| 20 | Arrival late transfer (B10) | not with Tower by 4 NM | `server/rules/rules_instruction.py` |
| 21 | CTR / CTA vertical limits | CTR SFC–2,500 ft, CTA 1,500–2,500 ft | `data/egkk.json` |
| 22 | Director vertical limits | KKAPP 0–5,500 ft, SPT 5,500–6,500 ft | `server/rules` (B3) |
| 23 | Readback-correction wording | "negative … I say again …" | `server/rules/rules_phraseology.py` (C6) |
| 24 | Pilot error rates | off 0%, low 5%, med 10%, high 15% | `server/pilot/errors.py` |
| 25 | Expected-approach request | 2 min after check-in (+0–19 s spread per callsign) | `server/sim/world.py` |
| 26 | Conflict inject | FL70, 250 kt, 90° crossing NE of the field, LoS after ~90 s | `server/sim/world.py` |
| 27 | Forecast conflict criterion (advisory) | below both 3 NM and 1,000 ft; localiser pairs ≥ 2.5 NM in trail excepted | `server/sim/forecast.py` |
| 28 | Forecast rollouts / horizon | 64 rollouts, 120 s at 5 s steps, recomputed every 2 sim s | `server/sim/forecast.py` |
| 29 | Forecast perturbations | reaction delay +N(0.5, 1.0) s clipped −0.5…4 s on pending clearances; wind ±N(0, 5) kt per component (shared per rollout); turn rate ×N(1, 0.10) clipped 0.7–1.2; climb/descent ×N(1, 0.15) clipped 0.6–1.4; TAS ×N(1, 0.02); speed targets +N(0, 5) kt; level-off overshoot U(0, 150) ft | `server/sim/forecast.py` |
| 30 | Forecast candidate pairs / publishing | within 6 NM, or within 20 NM and 3,000 ft or levels crossing; publish p ≥ 0.01, paths (8 samples, 15 s) at p ≥ 0.15 | `server/sim/forecast.py` |
| 31 | Forecast simplifications | holds flown as a right-hand orbit; vertical-rate constraints and conditionals ignored; pending clearances use the controller's intended values | `server/sim/forecast.py` |

## Post-MVP data checks

The following are planned validation items, not operationally verified values:

- Forecast conflict criteria and probability calibration: initial simulated threshold is <3 NM and <1,000 ft; verify against the simulator's defined conflict semantics and measure false positives/negatives across seeded runs. Do not treat it as an operational separation standard.
- Forecast perturbations and bounds: document wind/speed/response-time distributions, seed, horizon, rollout count, and runtime environment alongside every result.
- Forecast cost measured 14 Nov on Tudor's MacBook (Apple Silicon, Python 3.12, numpy 2.5): 40 aircraft in a dense block (778 candidate pairs) p50 18.7 ms / max 24.6 ms per pass; busy preset over 1 h (peak 33 aircraft) p50 6.0 ms, p95 7.0 ms, max 8.6 ms. Calibration (false positives/negatives against logged outcomes) not yet measured.
- Live traffic import: before enabling a provider, verify its official API, permitted use, freshness, attribution, rate limits, and field meanings. Record fetch time and source time, flag stale data, and retain a snapshot/replay fallback. Do not scrape websites or transmit control instructions.
- Benchmark claims: record dataset version, sample count, provider/model version, hardware, and measurement method; distinguish target thresholds from measured results.
