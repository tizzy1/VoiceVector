# SweatboxAI — Simulation Spec (aircraft, rules, airspace, radar, integration)

> Deterministic simulator for the Gatwick Director position. Realistic *enough*, never the focus — the voice agent is.
> Companion to `SWEATBOX_PLAN_V2.md` and `VOICE_PIPELINE.md`. Ground movement out of scope.
> Values marked **⚠ VERIFY** are defaults that Tudor must check before the demo.

---

## 0. Principles

1. **The sim is the single source of truth** for aircraft state, legality and timing. LLMs never move aircraft, never decide legality, never choose errors.
2. **One instruction registry** drives everything: parsing, validation, unable checks, application, readback slots, legality rules. Adding an instruction = one entry (§6.2).
3. **Event-sourced:** every transmission, parse, readback, state change and rule finding is an event with a sim timestamp. The end-of-session report is computed from the event log.
4. **Fixed 1 Hz tick** on the server; the browser interpolates to 60 fps.
5. **Realistic where visible** (turn rates, climb/descent rates, speed changes, ILS capture, procedure following); simple where invisible.

---

## 1. Aircraft model

### 1.1 State
```python
class AircraftState(BaseModel):
    callsign: str; spoken: str; actype: str; wake: Literal["H","M","L"]; persona: str
    flow: Literal["ARR","DEP"]
    phase: Literal["QUEUED","TAKEOFF","SID","STAR","VECTORS","APPROACH","FINAL",
                   "GO_AROUND","HOLD","LANDED","HANDED_OFF"]
    # kinematics
    x: float; y: float               # NM, local projection (origin = 26L threshold)
    alt: float; vs: float            # ft, ft/min
    hdg: float; track: float         # deg true
    ias: float; tas: float; gs: float
    # targets (autopilot)
    lat_mode: Literal["HDG","LNAV","LOC","HOLD","RWY_TRACK"]
    vert_mode: Literal["ALT","LVL_CHG","GS"]
    tgt_hdg: float | None; turn_dir: Literal["L","R"] | None
    route: list[str]; route_idx: int; route_name: str | None   # SID/STAR/direct
    cleared_alt: float; vs_constraint: tuple[str,int] | None   # ("max",1000) etc.
    tgt_ias: float | None; spd_constraint: Literal["exact","or_greater","or_less"] | None
    no_speed_restriction: bool
    # clearances & expectations
    approach_cleared: dict | None    # {"type":"ILS","runway":"26L"}
    loc_armed: bool; gs_armed: bool; established: bool
    expect: dict                     # {"approach":"ILS 26L","level":7000}
    conditionals: list[dict]         # pending triggered instructions
    squawk: str; qnh: int; freq_unit: str   # "director" | "tower" | "london"
    on_frequency: bool
```

### 1.2 Performance data (`data/perf.json`, from OpenAP)
Per type: `climb_fpm_low`, `climb_fpm_mid`, `descent_fpm`, `descent_fpm_low`, `descent_cas_kt`, `vapp_kt`, `min_clean_kt` (rough estimate), `ceiling_ft`, `wake`. Global: 250 kt below FL100, max bank 25°, accel 1.2 kt/s, decel 0.8 kt/s level.

Derived values the sim uses:
| Quantity | Rule |
|---|---|
| Min speed, clean | `min_clean_kt` |
| Min speed, configured (on approach) | `vapp_kt` + 5 |
| Max speed below FL100 | 250 kt unless `no_speed_restriction` |
| Max speed above FL100 (TMA) | 300 kt (sim cap) |
| Climb rate | `climb_fpm_low` below 10,000 ft, `climb_fpm_mid` above |
| Descent rate | `descent_fpm_low` below 10,000 ft, `descent_fpm` above |
| Expedite | ×1.5 vertical rate (capped 3,500 fpm) |

### 1.3 Kinematics (each tick, dt = 1 s)
- **TAS** = IAS × (1 + 0.02 × alt/1000). **GS/track** = TAS vector + scenario wind vector (constant per session; default calm, demo can set e.g. 240/15).
- **Speed:** move IAS toward target at accel/decel rates. **Descent–deceleration trade-off:** while descending at > 1,000 fpm, decel is halved (0.4 kt/s). This makes "descend and reduce" behave realistically. If both are urgent, the pilot prioritises the descent.
- **Turn rate** = min(3°/s, 1091 × tan(25°) / TAS) → ~2.4°/s at 210 kt, ~2.0°/s at 250 kt. Ramp roll in and out over 2 s (rate × 0.5 for the first and last 2 s) so the trail curves naturally.
- **Turn direction:** if `turn_dir` is set, turn that way even if longer. Otherwise take the shortest. A turn > 180° with an explicit direction is honoured.
- **Vertical:** move toward `cleared_alt` at the rate above, or the `vs_constraint`. **Level-off capture:** linearly reduce VS over the last 10% of the change (min 300 ft) → no overshoot (except injected level busts).
- **Position:** x += GS·dt/3600·sin(track); y += GS·dt/3600·cos(track).

### 1.4 Autopilot modes
| Mode | Behaviour |
|---|---|
| `HDG` | Fly `tgt_hdg` with `turn_dir` |
| `LNAV` | Fly to `route[route_idx]`; sequence when < 1 NM (or turn-anticipation distance ≈ turn radius × tan(Δ/2)); end of route → see §1.5 |
| `LOC` | Proportional cross-track correction to the localiser course (intercept heading = course ± min(30°, 10° × XTE NM)) |
| `HOLD` | Simplified racetrack at a fix: inbound course from data/default, right turns, 1-min legs ⚠ VERIFY per hold |
| `RWY_TRACK` | Departure runway track until 1,500 ft or the first SID fix |
| `ALT` | Hold `cleared_alt` |
| `LVL_CHG` | Climb/descend to `cleared_alt` |
| `GS` | Descend on 3° glidepath: target alt = 196 + 318 × dist_to_threshold (ft) |

### 1.5 Default behaviour when not instructed
| Situation | What the aircraft does |
|---|---|
| Arrival on STAR | Follows the STAR laterally, **holds cleared level** (descends only when instructed), 250 kt below FL100, otherwise descent speed |
| Reaches STAR end (WILLO / TIMBA) with no vectors | Enters the simplified hold at that fix — classic trainee failure mode, visible on radar |
| Departure after takeoff | Runway track → SID, climb to **6,000 ft** (initial SID altitude) and **level off**; accelerate to 250 kt above 3,000 ft |
| Departure reaches end of SID or 25 NM without "contact London" | Continues on last track at 6,000 ft → finding "departure not transferred" |
| Cleared ILS, on heading | Keeps heading until LOC capture, then descends on GS after capture |
| Established on LOC, cleared approach | Speed schedule: 180 kt until 8 NM, **160 kt until 4 NM** ⚠ VERIFY Gatwick standard, then Vapp |
| Final, < 0.5 NM, on GS | Lands → `LANDED`, removed after 30 s (runway occupied for `runway_occupancy_s`) |
| Told "contact Tower" | Off frequency; if not cleared for approach it still continues its last clearance (and the trainee can't reach it) |
| Go-around (instructed or injected) | Runway track, climb to **3,000 ft** ⚠ VERIFY missed-approach procedure, 180 kt, calls "going around" |

### 1.6 Lifecycle
```
ARR: (spawn at STAR entry) STAR ──vectors──► VECTORS ──cleared ILS──► APPROACH ──LOC+GS──► FINAL ──► LANDED
                             └─ STAR end ──► HOLD ──vectors──► VECTORS            └─ go-around ─► GO_AROUND ─► VECTORS
DEP: QUEUED ──release──► TAKEOFF ──Vr, 35 ft──► SID ──"contact London"──► HANDED_OFF
```
- **Takeoff:** accelerate on runway 3 kt/s to Vr (Vapp + 10), rotate, climb at `climb_fpm_low`, V2+10 to 1,500 ft, then accelerate.
- **Check-in calls:** arrivals on spawn ("Gatwick Director, Speedbird 117, flight level 90, WILLO 1G, information Kilo"); departures passing 2,000 ft ("…passing 2,000 climbing 6,000, BOGNA 1X").
- **Reports:** "established" when the LOC is captured, if asked to report.

### 1.7 Pilot-initiated behaviour
| Behaviour | Trigger |
|---|---|
| Request higher | Departure levelled at 6,000 for > 60 s |
| Ask expected approach | Arrival 2 min after check-in with no `expect` set |
| "Approaching WILLO, no further clearance" | Arrival within 3 NM of STAR end, no vectors |
| Unable / query | §2.3 |
| MAYDAY / PAN | Instructor inject only (voice via fal Chatterbox, stressed) |
| Low fuel, request priority | Optional inject |

All pilot speech goes through the voice pipeline's reply scheduler (frequency discipline, stepped-on logic).

---

## 2. Instruction catalogue

### 2.1 Accepted instructions
| Instruction | Example | Effect | Unable / query when | Readback must include |
|---|---|---|---|---|
| **Heading** | turn left heading 240 / fly heading 240 | `HDG`, tgt_hdg, turn_dir; cancels LNAV | — | heading (+ direction if given) |
| **Turn by** | turn right 20 degrees | tgt = hdg ± n | — | direction + degrees |
| **Present heading** | continue / fly present heading | tgt = current | — | "present heading" |
| **Climb/descend altitude** | descend altitude 4000 | cleared_alt, `LVL_CHG` | below min altitude without approach clearance → query; above transition altitude in "altitude" form → query (§3.3) | level + verb |
| **Climb/descend FL** | climb flight level 70 | as above | FL below transition level → query; above ceiling → unable | FL + verb |
| **Maintain** | maintain 5000 / stop descent at FL80 | cleared_alt = value (stop at) | — | level |
| **Rate / expedite** | descend at 1000 fpm or less / expedite descent | vs_constraint / ×1.5 | rate > type max → unable | rate / "expediting" |
| **Speed** | reduce 210 / speed 180 or greater | tgt_ias + constraint | < min_clean (not configured) → unable; > 250 below FL100 without restriction cancelled → unable; < Vapp+5 → unable | speed (+ constraint) |
| **Minimum clean / keep speed** | reduce minimum clean speed / maintain 220 | tgt_ias | — | as given |
| **Speed cancel** | no ATC speed restriction / resume normal speed | clears tgt; `no_speed_restriction` | — | phrase |
| **Direct** | proceed direct WILLO | `LNAV` [fix] (+ rest of procedure if the fix is on it) | fix unknown → "say again fix / spell" | fix |
| **Resume / rejoin** | resume own navigation / rejoin the BOGNA 1X | `LNAV` on remaining procedure (closest downstream fix) | no procedure → query | phrase |
| **Hold** | hold at TIMBA as published | `HOLD` at fix | fix not a hold → simplified hold anyway + note | fix (+ "as published") |
| **Approach clearance** | cleared ILS approach runway 26L | arms LOC + GS | runway inactive/unknown → query; no LOC intercept possible (§3.2) → unable "too high / angle" | type + runway |
| **Intercept localiser only** | …heading 290, intercept the localiser 26L | arms LOC only (no GS descent) | — | heading + "localiser" + runway |
| **Descend on glidepath** | (after LOC-only) descend on the ILS | arms GS | — | phrase |
| **Report** | report established / report speed | sets report flag / answers now from state | — | (reply is the report) |
| **Confirm** | confirm cleared level | answers from **believed** clearance (may contain an active error) | — | the value |
| **Traffic info** | traffic 2 o'clock 5 miles opposite direction 1000 ft above | "looking" → "traffic in sight" if within 5 NM and in front (random 50%) | — | "looking" |
| **Expect** | expect ILS approach 26L / expect vectors | stores `expect` | — | the expectation (light) |
| **Conditional** | after WILLO descend FL70 | registered trigger | condition unknown → query | condition + instruction |
| **Contact** | contact Gatwick Tower 124.225 / contact London 134.125 | off frequency (`freq_unit`) | wrong freq for unit → pilot reads back anyway (finding) | unit + frequency |
| **Squawk** | squawk 4521 / squawk ident | squawk | — | code |
| **QNH** | QNH 1013 | qnh | — | QNH |
| **Go around** | go around | `GO_AROUND` | — | "going around" |
| **Cancel approach** | cancel approach clearance, fly heading 360 | disarm LOC/GS | — | phrase + new instruction |
| **Say again / read back** | say again / read back | repeat / full readback | — | (repeat) |
| **Negative / I say again** | negative, descend altitude 4000 | correction flow (VOICE §14) | — | corrected elements only |

### 2.2 Recognised but not executed (pilot response + finding)
| Instruction | Why | Pilot says | Finding |
|---|---|---|---|
| Cleared to land / take-off | Tower's job, not Director's | "Confirm? We're with Director" | wrong-position instruction |
| Taxi / ground | Out of scope | "Say again" | — |
| Visual approach, RNP approach | Not modelled (no data) | "Unable, request ILS" | — |
| Re-route onto an unrelated STAR/SID | Not modelled | "Unable, request vectors" | — |
| Holding with custom entry/legs | Simplified holds only | reads back, flies the simplified hold | — |

### 2.3 Hard limits (never violated by the sim)
Ceiling; speed envelope (min clean / Vapp+5 / 250 below FL100 / 300 cap); vertical rates; bank 25°; GS capture only from **at or below** the glidepath; LOC capture only at intercept angle ≤ 45° and XTE < 2 NM (otherwise flies through and calls "overshooting the localiser").

---

## 3. Legality & rules engine

### 3.1 Architecture
```
Events (transmission, parse, readback, state snapshot, sim event)
   │
   ├─► Transmission-time checkers  (run when a controller transmission is parsed, against the state snapshot at that moment)
   ├─► Continuous checkers         (run every tick: separation, terrain, airspace, wake, deviations)
   └─► Lifecycle checkers          (error records: caught / missed; handoffs; landings)
           │
           ▼
   Finding {id, rule_id, category, severity, t, actor: CONTROLLER|PILOT|SYSTEM,
            callsigns, evidence: {tx_id, transcript, state_excerpt}, correct_practice}
```
Findings are **deterministic code**. The LLM only narrates them (§3.5).

### 3.2 Rule catalogue
**A. Safety — continuous (severity HIGH)**
| Rule | Check |
|---|---|
| A1 Loss of radar separation | < 3 NM and < 1,000 ft (excl. same-LOC pairs > 2.5 NM in trail, landed) — record start, end, minimum |
| A2 Predicted conflict ignored | STCA predicted LoS within 120 s and no relevant instruction to either aircraft within 60 s |
| A3 Wake on final | Follower inside wake minimum for the leader's category (`traffic.json`) ⚠ VERIFY UK values |
| A4 Below minimum altitude | Aircraft below area minimum (placeholder: **3,000 ft** outside the final approach area, **2,000 ft** within it; 196 + GS when established) ⚠ VERIFY → replace with the ATC Surveillance Minimum Altitude chart values per RMA area |
| A5 Outside controlled airspace | IFR aircraft below the base of the overlying CTA/TMA and outside the CTR |
| A6 Runway | Departure released with an arrival < `departure_release_gap_nm`, or two aircraft on the runway |

**B. Controller instruction legality — transmission time (MEDIUM/HIGH)**
| Rule | Check (against the snapshot at that moment) |
|---|---|
| B1 Descent below minimum altitude | Cleared level < area minimum and aircraft not established/cleared approach |
| B2 Vectoring out of CAS | Heading projected 2 min ahead leaves controlled airspace at the cleared level |
| B3 Level outside jurisdiction | Cleared level above Director airspace (KKAPP 0–5,500; SPT 5,500–6,500) without being a handoff level ⚠ VERIFY intent |
| B4 Bad ILS intercept | Approach clearance/intercept heading gives an intercept angle > 30° (warn) / > 45° (fail), aircraft above the glidepath at intercept, or intercept < 2 NM before FI26L ⚠ VERIFY |
| B5 Speed instruction outside envelope | Below min clean (not configured) or > 250 below FL100 without "no ATC speed restriction" |
| B6 Transition altitude/level misuse | "Altitude" above transition altitude (**6,000 ft** ⚠ VERIFY; altitude 6,000 itself is valid — it is the SID initial altitude) or "flight level" below the transition level; first altitude clearance without QNH |
| B7 Wrong-position instruction | Cleared to land/take-off; instructions to an aircraft already handed off |
| B8 Conflicting instruction set | Same-type instructions contradict within one transmission without "correction" |
| B9 Instruction into conflict | The instructed level/heading creates a predicted LoS within 2 min (sim projects it) |
| B10 Late transfer | Arrival not sent to Tower by 4 NM; departure not sent to London by the sector boundary / 25 NM |

**C. Phraseology — transmission time (LOW)**
| Rule | Check |
|---|---|
| C1 Missing callsign | No resolved callsign |
| C2 Missing level type | Level without "altitude"/"flight level" |
| C3 Ambiguous number | e.g. "climb two four zero" (level or heading?) — parser ambiguity |
| C4 Non-standard phrase | Matched only by the LLM fallback / known informal patterns ("down to", "go to") |
| C5 Overloaded transmission | > 3 instruction elements |
| C6 Readback correction phrasing | Correction without "negative … I say again" ⚠ VERIFY wording |

**D. Readback monitoring (controller duty)**
D1 readback error caught (positive) · D2 readback error missed · D3 incomplete readback accepted · D4 similar-callsign takeover not detected · D5 unnecessary correction.

**E. Pilot compliance monitoring (controller duty)**
E1 execution error caught · E2 missed (heading off by > 15° for > 30 s / wrong turn / level ≠ cleared ± 300 ft unnoticed) · E3 level bust detected.

**F. Efficiency (scored, not illegal)**
Track miles flown vs STAR-end→threshold optimum · spacing on final vs target (wake min + 0.5 NM) · departure release delay · time in holds · number of transmissions per aircraft.

### 3.3 Pilot-side legality (the pilot's own "mistakes" made visible)
Planned errors come from the error policy (VOICE §10). The rules engine records the pilot's deviation as `actor=PILOT` and judges the **controller** on whether it was caught (D/E). Unplanned pilot deviations are impossible by design (validator + deterministic flight).

### 3.4 Severity → score
HIGH −15, MEDIUM −5, LOW −1, positive catches +5 (cap +25); efficiency 0–20 bonus. Score shown with a breakdown — no black-box number.

### 3.5 End-of-session report
1. Deterministic `report.json`: findings, timeline, catch table, LoS list, efficiency, latency stats.
2. Claude Sonnet writes a narrative + coaching **only from `report.json`**, citing findings by id; the prompt forbids new findings.
3. UI: score + breakdown, timeline with clickable markers (jump to replay time with audio), findings grouped A–F with transcript snippets and the "correct practice" line, readback/error catch table, three coaching points.
4. Export (PDF/JSON). Stored per session (MongoDB if that bounty is taken).
5. The fal PersonaPlex instructor conversation is seeded from the same `report.json`.

### 3.6 Probabilistic conflict forecast (post-MVP technical depth)
The deterministic STCA remains the authoritative alert rule. Add a separate forecast engine that estimates uncertainty over plausible trajectories; forecast probabilities are advisory training visualization only and must not change legality decisions, pilot errors, clearances, or aircraft control.
- Recompute candidate pairs every 2 sim seconds, caching between passes. Prefilter to relevant aircraft (nearby/closing or within 6 NM; within 20 NM and 3,000 ft, or projected levels cross).
- Initial implementation: 64 seeded paired rollouts per candidate pair, 120 s horizon, 5 s steps. Perturb reaction delay, wind, turn/climb rates, speed and small level-off overshoot. Forecast uses controller-intended clearances and pending scheduled applications; it must not inspect planned pilot errors.
- `p_conflict` is the fraction of paired rollouts that cross below both 3 NM and 1,000 ft. Report median time and minimum horizontal/vertical separation. Publish paths only for pairs with probability ≥ 0.15, with a bounded/downsampled set of samples.
- Show risk on tags and paths on radar/viz; use a distinct `PROB_CONFLICT` alert only at the agreed high-probability/time threshold. The UI must label it as a forecast, not an observed loss of separation. Existing STCA_LOS/STCA_PRED continue unchanged.
- Seed all perturbations from session seed, simulation tick and pair identity so a replay is repeatable. Benchmark forecast cost at 40 aircraft; target ≤ 30 ms per pass, reducing sample count/horizon if needed. Calibration is optional and must use logged outcomes, not invented accuracy claims.

### 3.7 Live traffic snapshot import (post-MVP technical depth)
- Import is a one-time read-only snapshot. It converts fetched aircraft into local simulated AI-pilot aircraft; subsequent motion and controller actions belong to the simulator. A separate context mode may display non-controllable targets, but is optional.
- Normalize source records at the fetch boundary. For snapshot import, filter to airborne aircraft within 40 NM and 1,500–16,000 ft; classify Gatwick arrivals/departures from available flight-plan fields and use conservative heuristics only when fields are missing. Skip unrelated overflights in snapshot mode and report imported/skipped counts.
- Map aircraft type to the nearest supported performance model, preserve callsign when possible, avoid collisions with existing callsigns, and initialize a valid local state. Imported aircraft receive ordinary pilot voices/check-ins and deterministic simulator behavior. Apply STCA immediately; do not reject real observed traffic solely because it conflicts.
- Every imported track retains its source and fetch time for display/credit. Surface stale/error/empty results; external fetch failure must not interrupt the session.
- VATSIM is read-only: never log into the network, transmit, or control a real aircraft. FR24/OpenSky work is optional and only after verifying current official API docs, access terms, credentials and rate limits. Do not scrape websites.

---

## 4. Airspace

### 4.1 Data pack (`data/egkk.json` v2 — built by `tools/build_airspace.py`)
| Layer | Content |
|---|---|
| Runways | 08L/26R, 08R/26L thresholds, courses, elevation (26L/08R active) |
| Fixes | **131** fixes/navaids — every point on every procedure, all resolved |
| Procedures | **SIDs:** 26L ×17, 08R ×12 (RNAV + conventional). **STARs:** 16 for each runway, all ending at **WILLO** (9) or **TIMBA** (7). All **61** resolved |
| Airspace | Gatwick **CTR** (D, SFC–2,500), Gatwick **CTA** (D, 1,500–2,500), **London TMA** parts 1–24 (A, 2,500–FL75 bases → FL195), **Worthing CTA** 1–7 (A, FL65–FL105 bases) — with class and vertical limits |
| Director sectors | KKAPP_E / KKAPP_W lines (0–5,500 ft), SPT layers (5,500–6,500), KKFIN_E / KKFIN_W |
| RMA areas | Westerly/easterly minimum-altitude area boundaries (display only; values ⚠ VERIFY) |
| Video map | Extended centrelines; coastline/MSA optional later |

Clipped to 70 NM around the airport. Schema change from v1: `stars_26L`/`sids_26L` → `procedures.STAR["26L"][name].route` / `procedures.SID[...]`. Skip SID `BIG` (placeholder). No altitude/speed constraints on procedures (not in the source).

### 4.2 Jurisdiction
- **Director airspace** = KKAPP_E ∪ KKAPP_W lateral area, 0–5,500 ft (+ SPT to 6,500 when enabled). Arrivals enter from STAR ends/holds; departures leave via SIDs to London.
- Aircraft **outside** jurisdiction are shown (no tag highlight, dimmed) for context — e.g. VATSIM-imported traffic overhead.

### 4.3 Runway configuration
Westerly (26L) default; easterly (08R) selectable at session start — switches STAR/SID sets, centreline, ILS and RMA layer.

### 4.4 Layer toggles (radar)
CTR · CTA · TMA (with base labels) · Worthing CTA · Director sector lines · RMA areas · Fixes (labels on/off separately) · STARs · SIDs · Extended centreline + range ticks · Range rings · Holds · Airspace base labels · VATSIM context traffic.

---

## 5. Controller scope (operator view)

The operator scope and the live audience visualisation are separate interfaces. This section specifies the controller's operational workstation: a modern EuroScope-like layout and information hierarchy, with a large 2D radar and compact flight lists/panels. The projector/second-device view is a separate presentation surface and may be 3D; its design is covered by `PROJECT_REPORT.md` §5.2 and `ASSIGNMENT_BHAVIN.md` §5.3.

### 5.0 Workstation layout
- Keep the radar dominant in the centre, with a compact top status/toolbar and persistent, collapsible operational panes for arrivals, departures, selected-aircraft details, METAR/weather, transcript/communications and alerts/events.
- Arrival and departure lists should read like flight strips. Include callsign, aircraft type, wake, route/procedure, current and cleared level, speed, heading/assigned speed when available, phase/status, and sequencing/release information when available.
- Selecting a list row selects the same aircraft on the radar; selecting a radar track focuses its list row and details. Show assigned clearance alongside observed aircraft state so deviations are easy to spot.
- Provide an EGKK METAR panel with observation time, wind, visibility, cloud, temperature/dew point, QNH and raw report. Clearly show stale/unavailable data; do not represent placeholder values as a live METAR.
- Keep common controller actions and status visible. Visual polish may take priority over secondary functionality, but core track selection, pan/zoom, range, layer toggles, list selection/filtering, typed transmission and PTT must remain usable when implemented.
- This workstation is operational, not cinematic: no 3D airport scene or presentation camera modes in the controller scope.

### 5.1 Look
Modern take on the EuroScope philosophy: realistic content and information density, familiar controller workflow, calm modern chrome.
- Background: deep neutral slate (not pure black); map in low-contrast desaturated tones; **traffic is the brightest thing on screen**.
- Type: monospace for tags and data (JetBrains Mono / IBM Plex Mono), sans for UI.
- Colour semantics (few): assumed traffic white/near-white; unconcerned traffic grey; selected cyan; transmitting/addressed pulsing accent; STCA red, wake amber; departures/arrivals distinguished by a tag marker, not a rainbow.
- Thin 1 px lines, no glow, no gradients.

### 5.2 Track & data block
```
 ●  ── BAW117          callsign (bold when assumed)
       090↓040 210     current FL/alt (3 digits), trend arrow, cleared level, ground speed
       A320 H240 S210  type, assigned heading, assigned speed (only when set)
```
- Track symbol + **history dots** (last 5 positions) + **speed vector** (1/2/5 min, toggle).
- Leader line; **draggable tags**, auto-declutter on overlap.
- Tag states: assumed · unconcerned · selected · **transmitting / being addressed** (pulse, synced with captions) · STCA / wake alert · emergency (7700, distinct styling).
- Altitudes below the transition altitude as A040; above as FL.

### 5.3 Interaction
- **Pan:** drag. **Zoom:** wheel/pinch, anchored at the cursor; range presets (10/20/40/80 NM). Keyboard: `+`/`-`, arrows, `C` centre on airport, `F` follow selected.
- Click track = select (highlights in transcript and lists). Double-click = centre.
- **Range & bearing tool:** right-drag.
- Decorative EuroScope-style toolbar (MAP / RANGE / FILTER / STCA / VECT / ALT…): **RANGE, layer toggles and VECT work**; others present for realism.
- Bottom caption strip (live controller/pilot captions — reserved per earlier note).
- Operational panels: arrival list, departure list (with release info), selected-aircraft details, METAR, transcript with plain-English lines, alerts and latency readout. Row/track selection is synchronized as described in §5.0.

### 5.4 Rendering
- Canvas 2D (or PixiJS if slow). Static layers (airspace, fixes, procedures) cached to an offscreen canvas and redrawn only on pan/zoom/toggle. Traffic drawn per frame.
- **Interpolation:** keep the last two server states per aircraft; render at `t − 1 s` and lerp position/heading → smooth 60 fps with 1 Hz data.
- Screen transform: `screen = (xy_nm − centre) × px_per_nm`, y flipped.

---

## 6. Integration with the voice pipeline & LLM

### 6.1 Flow
```
PTT audio ─► Scribe ─► normalise ─► parser (regex → Haiku fallback)
                                       │ Transmission (receiver, intent, instructions[])
                                       ▼
                       sim.evaluate(tx, snapshot)  ◄── instruction registry
                         ├─ per instruction: OK | UNABLE(reason) | QUERY(reason)
                         └─ transmission-time legality findings (B, C rules)
                                       │
                         pilot decision (VOICE §9) ─► error policy (VOICE §10)
                                       │ PendingClearance (intended / readback / flown)
                         ┌─────────────┴─────────────┐
                 ReadbackPlan ─► variant engine     sim.schedule_apply(elements, at = readback_end + reaction)
                  ─► validator ─► TTS                      │
                                                    continuous rules (A, E) each tick
                                       ▼
                               event log ─► report.json ─► Claude narrative / fal instructor
```

### 6.2 Instruction registry (single source of truth)
```python
@register("heading")
class HeadingInstr(InstructionSpec):
    patterns = [r"turn (left|right) heading (\d{3})", r"fly heading (\d{3})"]  # parser
    readback_slots = ["turn_dir", "value"]                                       # readback + validator
    def evaluate(self, ac, instr, world) -> Evaluation: ...                      # OK/UNABLE/QUERY
    def apply(self, ac, element): ...                                            # sets autopilot targets
    def legality(self, ac, instr, world) -> list[Finding]: ...                   # B-rules for this type
```
The parser grammar, LLM-fallback schema enum, unable checks, sim application, readback templates' slot list and the legality checks all come from this registry — so voice and sim can't drift apart.

### 6.3 Contracts between voice and sim
| Call | Direction | Returns |
|---|---|---|
| `sim.snapshot()` | voice → sim | frozen state for evaluation (also stored with the event) |
| `sim.evaluate(tx)` | voice → sim | per-instruction verdicts + findings |
| `sim.schedule_apply(callsign, elements, t)` | voice → sim | ack; executes at sim time `t` using `flown_value` |
| `sim.on(event)` | sim → voice | pilot-initiated calls (check-in, requests, "established", go-around) → reply scheduler |
| `rules.findings(session)` | report | all findings |

### 6.4 LLM roles (and non-roles)
| Does | Never |
|---|---|
| Parser fallback (Haiku) | Move aircraft |
| Readback variant bank at startup | Decide legality or findings |
| Report narrative (Sonnet) | Choose errors |
| Scenario-from-sentence → generator params | Write numbers into readbacks |

### 6.5 Timing
Sim time = wall time (pause supported). Transmission processing is async; application is scheduled in sim time, so pausing the sim also pauses pending applications. Snapshots are taken at **PTT up**, so legality reflects what was true when the controller spoke.

---

## 7. Build order (Tudor's lane)

| By | Done |
|---|---|
| 15:00 | State model, kinematics, HDG/ALT/LVL_CHG/speed; typed commands; 1 Hz WS state |
| 16:00 | LNAV on STAR/SID from `procedures`; arrival spawner; default behaviours (§1.5) |
| 18:00 | LOC/GS capture, final speed schedule, landing; departures + runway manager |
| 20:00 | Registry wired to parser; `evaluate` (unable/query); A1/A2/A3 continuous rules |
| 22:00 | Conditionals, holds, go-around, contact/off-frequency; B1/B4/B6/B10 rules |
| Sun 08:00 | Remaining B/C/D/E/F rules; `report.json`; narrative prompt |
| Sun 10:00 | Easterly config, VERIFY values filled in, polish |

**Cut order:** easterly config → holds (keep the STAR-end loiter) → conditionals → C-rules → F efficiency. **Never cut:** kinematics, STAR/SID following, ILS + landing, A1–A3, B1, B4, readback D-rules, report.

---

## 8. Behaviour tests
1. From 270, "turn left heading 240" → heading decreases monotonically; turn rate ≤ bank-limited value.
2. "Turn right heading 180" from 270 → goes the long way via 360.
3. "Descend altitude 4000" from FL90 → levels 4,000 ± 50 ft, no overshoot; time ≈ 5,000 / descent rate.
4. Descend + reduce simultaneously → decel halves during descent.
5. 30° intercept + cleared ILS 26L → LOC captured, GS captured from below, speed 160 → Vapp at 4 NM, lands at the threshold.
6. Intercept from above the glidepath → no GS capture; pilot "unable, too high".
7. 60° intercept → overshoots the localiser; pilot calls it; B4 finding.
8. STAR flown to WILLO with no vectors → enters hold; "no further clearance" call.
9. Departure on BOGNA1X levels at 6,000; requests higher after 60 s; B10 if not transferred.
10. Two arrivals converging at the same level → A1 with start/end/min distance.
11. "Descend altitude 2000" 20 NM out, not cleared approach → B1 finding + pilot query.
12. "Reduce 150" at FL90 → unable; B5 finding.
13. Pause → pending applications don't fire until resume.

---

## 9. ⚠ VERIFY list (Tudor)
| Item | Default used |
|---|---|
| Wake minima on final (UK / RECAT-EU) | ICAO distances in `traffic.json` |
| Area minimum altitudes per RMA area | 3,000 ft general / 2,000 ft final area |
| Transition altitude / level | TA 6,000 ft; TL from QNH |
| Final approach speed control | 160 kt to 4 NM |
| ILS intercept limits | 30° warn / 45° fail; intercept ≥ 2 NM before FI26L |
| Missed approach 26L / 08R | runway track, 3,000 ft |
| Hold details (WILLO, TIMBA) | right turns, 1-min legs, inbound = track from the previous STAR fix |
| SID initial altitude | 6,000 ft |
| CTR/CTA vertical limits | CTR SFC–2,500, CTA 1,500–2,500 |
| Readback-correction phraseology | "negative … I say again …" |
| Director vertical limits / SPT use | KKAPP 0–5,500, SPT 5,500–6,500 |
