# Assignment — Tudor: Simulation, Pilot Logic, Rules & Data

> For Tudor and his AI agent. Read together with `PROJECT_REPORT.md`, `CONTRACTS.md`, `server/contracts.py`, `SIM_SPEC.md` (your main spec) and `VOICE_PIPELINE.md` §8–§14 (pilot decision, errors, corrections).
> **You are the aviation brain:** everything that decides what an aircraft is, does, may do, gets wrong, and whether the controller acted legally.

---

## 1. Mission
Build a deterministic Gatwick simulator (`World`), the pilot decision + error + correction logic (`PilotEngine`), and the legality rules engine + session report (`RulesEngine`). Own the data files and sign off all aviation content (phraseology, values marked ⚠ VERIFY). You also give the pitch.

## 2. You own
```
data/egkk.json  perf.json  telephony.json  traffic.json  scenario_demo.json   (+ review readback_variants.json)
server/sim/      world.py (class World) aircraft.py kinematics.py modes.py procedures.py
                 traffic.py runway.py stca.py registry.py airspace.py geo.py
server/pilot/    engine.py (class PilotEngine) decision.py errors.py clearance.py
server/rules/    engine.py (class RulesEngine) rules_safety.py rules_instruction.py
                 rules_phraseology.py rules_monitoring.py rules_efficiency.py report.py
server/tests/sim  server/tests/pilot  server/tests/rules
```
Do not edit other folders. Contract changes → `CONTRACTS.md` §7.

## 3. Interfaces you PROVIDE (exact — see `contracts.py`)
### 3.1 `class World(WorldAPI)` — constructor `World(bus: EventBus, data_dir: Path)`
| Member | Must do |
|---|---|
| `t: float` | Sim time in seconds since session start |
| `configure(settings)` | Apply runway, traffic rates; keep seed |
| `load_scenario(name, seed)` | Reset; `"demo"` loads `scenario_demo.json` exactly (deterministic); others start empty + generator |
| `tick(dt)` | Advance `dt` sim seconds: scheduled applications due, kinematics, modes, procedures, traffic generator, runway manager, STCA alerts, pilot-initiated calls. Emits sim events (CONTRACTS §3) |
| `public_state()` | `StateMsg` with every field of `AircraftPublic` filled, incl. **lat/lon** (inverse of the local projection) and `spoken_plain` ("British Airways 117") and `plain_status` ("descending to 4,000 ft · 210 kt") |
| `snapshot()` | `WorldSnapshot` (full state dicts) |
| `callsigns()` | `CallsignInfo` for aircraft on frequency: `telephony`, `spoken` ("speedbird one one seven"; suffix letters phonetic, "easy four five alpha bravo"), `spoken_digits` ("speedbird 117") |
| `fixes()` | Fix names usable in instructions |
| `evaluate(tx)` | One `Evaluation` per instruction: `OK` / `UNABLE` / `QUERY` + `ReasonCode` + values (SIM_SPEC §2). Pure: **no state change** |
| `schedule_apply(cs, elements, at)` | Queue; at sim time `at + element.delay_s` apply `element.flown` to the autopilot (SIM_SPEC §1.4–1.5). Handles conditionals and `ignores_instruction` (flown type unchanged = no-op) |
| `set_transmitting(cs, on)` | Sets `AircraftPublic.transmitting` |
| `inject(kind, cs, params)` | `mayday` (emergency, 7700, pilot_call kind mayday), `pan`, `go_around`, `radio_failure` (no replies), `spawn_conflict` (two arrivals on a deterministic converging geometry, same level, LoS in ~90 s if untouched), `force_overlap` (two pilot_calls in the same tick), `spawn_arrival`, `spawn_departure`. Emits `inject` |

`pilot_call` event `data` contract: `{"kind": ReplyKind, ...values}`. Kinds you emit: `checkin` (`level_fl`/`alt`, `route`, `atis`), `request` (`what`: "higher" | "expected_approach"), `no_further_clearance` (`fix`), `established` (`runway`), `going_around`, `mayday` (`reason`), `pan`.

### 3.2 `class PilotEngine(PilotAPI)` — constructor `PilotEngine(world: WorldAPI, bus: EventBus, data_dir: Path)`
| Method | Must do |
|---|---|
| `handle_transmission(tx, evals)` | Return `list[PilotResponse]` (usually 1; 0 → silence). Implements VOICE §8 (responder resolution), §9 (decision), §10 (errors), §14 (corrections, confirm, read back, say again). Builds `PendingClearance` per aircraft |
| `on_sim_event(e)` | For `pilot_call` → `PilotResponse` with `plan.kind` = the call kind and `report_values`; else `None` |
| `on_readback_spoken(resp, t_end)` | Mark errors `SPOKEN` (`error_spoken` event), start catch timers |
| `on_blocked(cs, tx_id)` | Return a `recall` response (`reaction_delay_s` 3–6 s, seeded) or `None` |
| `tick(t)` | Expire timers → `error_missed` (readback 30 s / execution 60 s or on next non-correction transmission to that aircraft) |
| `set_error_rate(rate)` / `force_error(mode, kind, cs)` | Error policy control (demo beats 4 and 5) |

**Response-building rules (the composer depends on these):**
- `plan.elements` = the **readback** `Instruction`s exactly as the pilot will say them (planned errors already applied, omitted elements removed). The composer reads back *all and only* these.
- `apply` = `ClearanceElement`s with `flown` values. Never put an element in `apply` for unable/query/report replies.
- `partial_correction`: `plan.elements` = only the elements whose value changed versus the pilot's believed clearance.
- `unable`/`query`: `plan.reason_code` + `reason_values` (e.g. `{"speed":150,"min_speed":210}`).
- `report`: `plan.report_values` (e.g. `{"cleared_alt": 5000}` — the **believed** value, which may contain an active error).
- `similar_callsign_takeover`: `PilotResponse.callsign` = the *wrong* aircraft, `plan.callsign` = that aircraft; its `apply` takes the clearance.
- `reaction_delay_s` by persona: crisp 0.8–1.2, calm_longhaul 1.0–1.5, student 1.5–2.5, rushed 0.5–0.9, stressed 0.4–0.8. `apply_delay_s` U(0.5, 2.0). Seeded.
- Persona per aircraft assigned at spawn (deterministic from seed), stored in the aircraft state.

### 3.3 `class RulesEngine(RulesAPI)` — constructor `RulesEngine(world: WorldAPI, bus: EventBus, data_dir: Path)`
| Method | Must do |
|---|---|
| `on_event(e)` | Transmission-time rules on `ctrl_transmission` (uses `data.tx`, `data.evals`, `world.snapshot()`), error lifecycle → D/E findings, comms timing stats |
| `on_tick(snap)` | Continuous rules (A-group, E2) |
| `report(session_id)` | `Report` (score + breakdown, findings, errors, stats incl. latency p50/p90 from event timings, filtered timeline). `narrative` left empty — Sanju fills it |
Emit each finding as a `finding` event too (the feed shows them).

## 4. Interfaces you CONSUME
| From | What |
|---|---|
| Bhavin | `EventBus` (`emit`, `subscribe`, `now`); the session calls `world.tick`, `pilot.tick`, `rules.on_tick` each second and routes controls to your methods |
| Sanju | `Transmission` objects (via `handle_transmission` / `evaluate`); `voice.speak` calls your `on_readback_spoken`, `schedule_apply`, `set_transmitting`, `on_blocked` |

## 5. Requirements

### MUST (by 22:00 MVP)
1. **Stubs by 15:00** (CONTRACTS §6): straight-line `World`, trivial `PilotEngine`, all methods present.
2. **Kinematics** (SIM_SPEC §1.3): bank-limited turn rate `min(3, 1091·tan25°/TAS)`, honour `turn_dir`, type-based climb/descent (`perf.json`), smooth level-off, accel/decel with the descent trade-off, wind vector.
3. **Modes**: HDG, LNAV (STAR from `egkk.json → procedures.STAR["26L"][name].route`), LOC, GS, ALT, LVL_CHG; default behaviours (SIM_SPEC §1.5) incl. STAR-end hold/loiter at WILLO/TIMBA.
4. **ILS**: LOC capture ≤ 45°, XTE < 2 NM; GS capture from at/below only; speed schedule 180 → 160 to 4 NM → Vapp; landing < 0.5 NM → `landing` event, removal.
5. **Arrival generator** (PLAN_V2 §9 / `traffic.json`): rates, spawn spacing ≥ 10 NM at the entry, level ≥ 1,000 ft from neighbours, 3-min feasibility probe, backlog; seeded; check-in `pilot_call` on spawn.
6. **STCA alerts**: LoS (3 NM / 1,000 ft) and 120 s prediction → `Alert`s in `public_state` + `alert_start/end` events.
7. **`evaluate`**: unable/query rules for speed envelope, ceiling, intercept angle, above-glidepath, unknown fix/runway, below-minimum without approach clearance, wrong position (cleared to land/take-off).
8. **PilotEngine core**: responder resolution; full readback responses; **readback errors** (consistent + readback_only modes, kinds: wrong_value_adjacent, digit_transposition, turn_direction_swap, level_type_confusion); guards (≤ 1 per aircraft, ≤ 2 global, never LoS within 30 s); **readback corrections → partial_correction**; confirm → report; read back → full; say again → repeat_last; missed timers in `tick`.
9. **Rules (MUST set)**: A1 LoS, A3 wake on final, B1 descent below minimum, B4 bad intercept, D1/D2 readback errors caught/missed, E1/E2 execution caught/missed; `report()` with score.

### SHOULD (by 02:00)
10. **Departures** + runway manager: queue, release conditions (runway occupancy, arrival gap ≥ 6 NM, departure separation incl. wake), takeoff → `RWY_TRACK` → SID (start with **BOGNA1X, NOVMA1X, SFD1X**), level off 6,000 ft, `request higher` after 60 s, handoff on "contact London".
11. **Execution errors**: wrong_turn_direction, wrong_value_flown, level_bust_overshoot, delayed_compliance; corrected by "I say again …" (VOICE §14 B).
12. **Injects**: mayday, go_around, spawn_conflict, force_overlap, radio_failure.
13. **Rules**: A2, A4 (placeholder minima), B5, B6 (TA 6,000 ft), B10, C1, C2, D3, D5.
14. **Demo scenario tuning**: `scenario_demo.json` + seed so beats 2–6 (PROJECT_REPORT §6.2) land reliably within the 3-minute script.

### COULD (Sunday morning)
15. Remaining rules (B2, B3, B7–B9, C3–C6, E3, F-group); holds with published inbound; conditionals; similar_callsign_takeover / omitted_element / wrong_callsign errors; easterly 08R config; VATSIM import.

### Data & content tasks
- Add `_airline_names` (ICAO → "British Airways", "easyJet", …) to `telephony.json` (done) for `spoken_plain`.
- Write the **parser golden set** `server/tests/voice/golden_parse.jsonl` (60–100 lines: real controller phrasing incl. sloppy variants → expected `Transmission.instructions`) — give to Sanju by **19:00** (first 30 lines by 16:00).
- Review `data/readback_variants.json` (Sanju generates ~22:00) for phraseology legality; mark bad variant ids.
- Fill the ⚠ VERIFY table (SIM_SPEC §9) and update `traffic.json` separation/wake values by 09:00.

## 6. Implementation notes
- `geo.py`: `to_xy(lat, lon)` / `to_latlon(x, y)` using `_meta.origin` (`x = Δlon·60·cos(lat0)`, `y = Δlat·60`). Altitudes in ft everywhere.
- `registry.py`: one `InstructionSpec` per `InstrType` with `evaluate()` and `apply()`; Sanju's parser keeps its own patterns but the **type list and field meanings** are fixed by `contracts.py`. If you need a field, follow CONTRACTS §7.
- Keep `World.tick` O(n²) STCA fine for ≤ 40 aircraft.
- All randomness: `random.Random(seed + salt)` per subsystem (traffic, personas, errors, delays).
- `plain_status` examples: "descending to 4,000 ft · 210 kt", "established ILS 26L · 160 kt", "climbing to 6,000 ft · BOGNA 1X", "holding at WILLO".
- Emit `instruction_applied` with `{element_type, value}` when each element takes effect (the viz uses it for "BA117 turning left" flashes).

## 7. Acceptance tests (put in `server/tests/`)
- SIM_SPEC §8 tests 1–13.
- Pilot: (a) consistent altitude error → `plan.elements` carries the wrong value, `apply.flown` carries it too; (b) correction "negative descend altitude 4000" → one `partial_correction` response whose `plan.elements` contains only the altitude; (c) full 3-element repeat after a 1-element error → only that element; (d) unnecessary correction → `finding` D5 and re-read of the mentioned element; (e) no correction for 30 s → `error_missed`; (f) wrong-turn execution error corrected → turn reverses; (g) the guard never produces LoS < 30 s over 500 seeded runs.
- Rules: LoS finding has start/end/minimum; B1 fires for "descend altitude 2000" 20 NM out without approach clearance; `report()` validates as `Report`.

## 8. Timeline & checkpoints
| Time | Deliver |
|---|---|
| 14:30–15:00 | Stubs (World + PilotEngine) on `main` |
| 15:00–16:00 | Kinematics, HDG/ALT/speed apply, lat/lon, callsigns → **16:00 integration 1** |
| 16:00–19:00 | STAR LNAV, arrival generator, ILS + landing, STCA, check-in calls; golden set to Sanju |
| 19:00–22:00 | `evaluate`, readback errors, corrections/confirm/read back, timers, MUST rules + `report()` → **22:00 MVP** |
| 22:00–03:00 | Departures (3 SIDs) + runway manager, execution errors, injects, SHOULD rules, demo scenario |
| 03:00–06:30 | Sleep |
| 06:30–09:00 | VERIFY values, variants review, COULD items, demo seed rehearsal with beats → **09:00 freeze** |
| 09:00–10:30 | Pitch slides content, README aviation section, Q&A prep |
| 10:30–11:30 | Rehearse ×3, submit |

## 9. Overflow rule
If Bhavin is behind at 22:00, you take the **report UI** on Sunday morning (you own the report content). If you're behind, cut in this order: COULD rules → execution errors beyond wrong-turn → departures beyond BOGNA1X → holds (keep STAR-end loiter).

## 10. Definition of done
All MUST items pass their tests; `World`, `PilotEngine`, `RulesEngine` satisfy the Protocols (`tests/integration/test_contracts.py` green); the seeded demo replays identically twice in a row; every ⚠ VERIFY value checked.

## 11. Post-MVP technical-depth track (planned)
Start only once the end-to-end MVP works. Own the simulation-side forecast and live-traffic normalization; these are advisory/context features and do not alter deterministic control behavior.

- Add `server/sim/forecast.py`: seeded paired Monte Carlo rollouts (initial target 64) over a 120-second horizon at 5-second steps, perturbing wind, speed, and pilot response timing. Estimate conflict likelihood from simulated separation below 3 NM and 1,000 ft; preserve seed, horizon, rollout count, threshold, and forecast time in the result.
- Reuse the existing simulation dynamics and clearance intentions. Forecast the currently intended clearances; do not inject forecast-only pilot mistakes. Bound runtime and workload (initial target ≤30 ms for 40 aircraft), expose uncertainty, and never use forecast output to change aircraft state or replace STCA.
- Add `server/sim/live_import.py` and an explicit `World.import_live(snapshot)` normalization boundary. Preserve source identity, source timestamp, fetch timestamp, and attribution; map only supported fields and reject stale/invalid/out-of-area records visibly.
- Live targets become simulated AI-pilot aircraft after import. They may be tracked and cleared inside this simulation, but the system must not transmit, connect-control, or issue commands to VATSIM or real aircraft. Keep live import optional and keep recorded/demo snapshots as a fallback.
- Verify each provider's official API access, terms, attribution, rate limits, and permitted use before enabling it. Use official interfaces only; no website scraping. Mark unverified thresholds as assumptions in `data/VERIFY.md`.
- Add seeded forecast tests for reproducibility, calibration/false-positive measurement, and load bounds, plus import normalization/staleness tests. Publish observed results separately from target thresholds.
