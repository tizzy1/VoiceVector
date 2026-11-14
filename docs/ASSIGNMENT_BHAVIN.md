# Assignment — Bhavin: Platform, Controller Scope, Visualisation & Demo

> For Bhavin and his AI agent. Read together with `PROJECT_REPORT.md` (esp. §3, §5, §6, §9), `CONTRACTS.md`, `server/contracts.py`, `web/src/protocol.ts`, and `SIM_SPEC.md` §4–§5 (airspace layers, radar display).
> **You own everything the judges look at and everything that connects the parts:** the server shell and WebSocket hub, both front-end apps, the design system, controls, the demo console, two-device mode, the report screen, and the submission assets.

---

## 1. Mission
Give the project a platform that never blocks the other two, a radar that looks like a modern professional tool, and a visualisation mode so polished and legible that judges understand an ATC session at a glance — then package it for submission.

## 2. You own
```
CLAUDE.md  README.md  .env.example  repo scaffold
server/contracts.py  web/src/protocol.ts          (merge owner — changes per CONTRACTS §7)
server/main.py  hub.py  session.py  feed.py
server/tests/integration/   (test_contracts.py, smoke_test.py)
web/  (everything except web/src/audio/):
  src/app/     shell, routing (/ radar, /viz, /report), ws client, zustand store, interpolation
  src/radar/   canvas renderer, layers, tags, tools, panels
  src/viz/     MapLibre + deck.gl scene, cameras, overlays
  src/ui/      design tokens, components, caption strip, controls, demo console, event feed
  src/report/  report screen
```

## 3. Interfaces you PROVIDE
### 3.1 Server
| Item | Must do |
|---|---|
| `EventLog` (implements `EventBus`) | `emit` (sync fan-out to subscribers; exceptions in a subscriber are logged, never propagate), `subscribe`, `now()` via `clock`; append JSONL to `runs/<session_id>/events.jsonl` |
| `Hub` (implements `Broadcaster`) | Per-client async send queues; `send(msg, roles)`; never let one slow client block others; under backpressure drop **stale `state`** messages only (never audio/caption/event/report) |
| `session.py` | Wiring exactly as CONTRACTS §2; sim loop (1 tick per `1/sim_speed` s, pause support); `handle_control` per CONTRACTS §2.3; beat table (§5.5 below); `reveal_errors` → `voice.set_reveal_errors(on)` |
| `main.py` | FastAPI: static `web/dist`, `/`, `/viz`, `/report`, `/api/data/{name}`, `/api/session`, `WS /ws?role=&session=`; binds `0.0.0.0:8000`; computes `join_url` from the LAN IP |
| `feed.py` | `Event → feed_text` templates ("BAW117 cleared ILS 26L", "Readback error caught ✓ EZY45AB", "Loss of separation BAW117 / EZY45AB", "Speedbird 117 landed") |
| WS routing | JSON `ptt`/`typed` and binary frames from `role=radar` → `voice.on_ptt / on_typed / on_audio_in`; `control` from any role → `handle_control`; on connect send `SessionMsg` + latest `StateMsg` |

### 3.2 Front end
`/` controller scope, `/viz` live audience visualisation, `/report` report view — all built from one Vite project, sharing the WS client, store and selected common components. The controller scope and audience visualisation are separate products: keep `/` focused on EuroScope-like controller workflow; `/viz` is the presentation surface and may be 3D.

## 4. Interfaces you CONSUME
| From | What |
|---|---|
| Tudor | `World`, `PilotEngine`, `RulesEngine` (constructors + methods in CONTRACTS §2); `StateMsg`, `Alert`s, events, `Report` |
| Sanju | `VoicePipeline` (constructor + `on_ptt/on_audio_in/on_typed`), `Narrative.write`, `CaptionMsg`/audio messages, **`createAudioEngine()`** from `web/src/audio` |

## 5. Requirements

### 5.1 MUST — platform (by 15:00, before anything else)
1. **Repo scaffold** (by 14:45): folders per CONTRACTS §1, docs into `docs/`, `contracts.py`, `protocol.ts`, `CLAUDE.md`, `.env.example`, Vite React TS app, FastAPI skeleton, `pyproject`/`requirements.txt`, `npm run build` → `web/dist`. Push; invite teammates.
2. **Server shell + stubs wiring** (by 15:00) and `tests/integration/test_contracts.py` (validates sample messages from each module) + `smoke_test.py` (starts the server, connects a radar and a viz client, sends a typed command, asserts a `caption` and a heading change in `state` within 5 s).

### 5.2 MUST — controller scope (by 22:00)
Spec: SIM_SPEC §5, PROJECT_REPORT §5.1, §5.3, §5.4.
3. **Canvas renderer** with an offscreen cache for static layers; traffic redrawn per frame; 60 fps with 40 aircraft.
4. **Interpolation**: keep the last two `state`s per aircraft, render at `t − 1 s`, lerp x/y/alt, shortest-arc lerp heading.
5. **Map layers** from `/api/data/egkk`: Gatwick CTR/CTA, London TMA (with base labels), Worthing CTA, Director sector lines (`sectors.lines`), RMA areas, fixes (+ labels), STARs/SIDs for the active runway (`procedures`), extended centrelines with range ticks, range rings. **Every layer toggleable** (toolbar + keyboard).
6. **Pan/zoom**: drag, wheel/pinch anchored at the cursor, range presets 10/20/40/80 NM, `+`/`-`, arrows, `C` centre, `F` follow selected.
7. **Track + data block**: symbol, history dots (last 5), speed vector (1/2/5 min toggle), leader line; tag lines `CALLSIGN` / `alt(3-digit FL or A040) ↑↓ cleared  gs` / `type  Hhdg  Sspd` (assigned values only when set); states: unconcerned/out-of-jurisdiction dimmed, selected, **transmitting pulse** (from `AircraftPublic.transmitting` + `AudioEngine.onActivity`), STCA red flash (from `alerts`), wake amber, emergency.
8. **Caption strip** (bottom; shared component with viz): controller partial text (muted) → final (solid, `chips` highlighted, latency badge); pilot captions revealed word by word using `pilotWordClock`; last 3 transmissions visible, older fade.
9. **PTT**: Space + on-screen button → `AudioEngine.pttDown/Up`; VU meter from `micLevel()`; PTT state indicator; ignore key repeat.
10. **Typed command box** (always visible) → `typed` message.
11. **Operational layout and flight lists**: preserve a professional EuroScope-like arrangement with a dominant central 2D scope and persistent, compact operational panes for arrivals, departures, selected-aircraft details, METAR/weather, transcript (with `plain_english` under each), alerts/event feed and latency (p50 of the last 10). Arrival/departure rows show callsign, type, wake, route/procedure, current and cleared level, speed, heading/assigned speed when available, phase/status, and sequencing/release details when available. Selecting a row selects/focuses that aircraft on the scope; selecting a track focuses the corresponding row and details. Make assigned clearance easy to compare with observed state. Show EGKK METAR observation time, wind, visibility, cloud, temperature/dew point, QNH and raw report; mark stale/unavailable weather clearly and never present placeholder values as live METAR. Panes should be resizable/collapsible where practical and must not bury the scope.
12. **Session controls panel** (PROJECT_REPORT §6.1): new session, scenario, runway, traffic preset + sliders, error rate, pause/resume, speed, injects, layer toggles, end session, second-screen QR + code.
13. **Compact scope toolbar** (MAP/RANGE/FILTER/STCA/VECT/ALT…): RANGE, VECT, filtering and layer toggles work. Other controls must be clearly disabled or decorative rather than implying unavailable functionality.

### 5.3 SHOULD — live audience visualisation (by 02:00)
Spec: PROJECT_REPORT §3, §5.2, §5.3.
This is a separate second-device/projector view. It may use a stylised 3D scene and presentation camera modes; it must not replace or dictate the controller scope layout.
14. **Scene**: MapLibre GL (OpenFreeMap style; fallback dark background + coastline) + deck.gl overlay; tilted 3D.
15. **Aircraft**: silhouette icons (`IconLayer`, rotated by track, coloured by flow); altitude stems (`LineLayer` to ground); fading trails at true height (`PathLayer`, last 3 min); ft → m for elevation.
16. **Context**: translucent glideslope ramp into the active runway (`SolidPolygonLayer` extruded), procedure lines in the air (light), airspace polygons faint, runway highlight.
17. **Labels**: plain English (`spoken_plain` + `plain_status`) via `TextLayer`; speech bubble with the live caption over the transmitting aircraft.
18. **Cameras**: Overview · Follow transmitting (auto "director's cut" — switches to whoever is transmitting, min 6 s dwell) · **Chase cam on final** (behind and above the aircraft along its track, pitch ~70°) · Departure cam · Free orbit. Transitions = `flyTo` 1.6–2.4 s ease-in-out, never cuts.
19. **Industrial control-room UI**: top status bar (session clock, traffic count, runway, latency, LIVE), left rail of flight-strip cards, right event feed (`EventMsg.feed_text`, animated in), bottom caption strip. Tokens in PROJECT_REPORT §5.2; motion in §5.3; respect `prefers-reduced-motion`.
20. **Room audio**: `AudioEngine.setPlayback(true)` on viz; radar default on (headset). Toggle in both.

### 5.4 SHOULD — two-device mode (by 00:00, test on venue Wi-Fi by 23:00)
21. Session code (4 chars) + QR of `join_url` (`/viz?session=CODE`) in the radar controls; viz connects as `role=viz`; auto-reconnect with backoff and state resync.
22. Fallback documented in README: phone hotspot → extended display (open `/viz` in a second window on the projector) → tunnel.

### 5.5 SHOULD — demo console (by 02:00)
23. Hotkeys on both views + hidden panel (`D`): `1` load demo scenario · `2` spawn conflict · `3` force stepped-on · `4` force readback error · `5` force wrong-turn error · `6` MAYDAY · `7` chase cam on next landing (client) · `8` end session → report · `J` judge mode (big PTT button, captions ×1.5) · `R` reveal errors toggle.
24. Server beat table (`handle_control` action `beat`, `params.key`):
| key | call |
|---|---|
| 1 | `world.load_scenario("demo", seed)` |
| 2 | `world.inject("spawn_conflict")` |
| 3 | `world.inject("force_overlap")` |
| 4 | `pilot.force_error("consistent", "wrong_value_adjacent")` |
| 5 | `pilot.force_error("execution_only", "wrong_turn_direction")` |
| 6 | `world.inject("mayday")` (callsign: first arrival in the demo) |
| 8 | end session (report flow) |

### 5.6 SHOULD — report screen (Sunday 05:30–09:00)
25. `/report` and an overlay on both views after `ReportMsg`: animated score + breakdown, timeline with markers (click → event detail), findings grouped A–F with transcript snippet + correct-practice line, readback/error catch table, narrative + coaching, latency stats. (Overflow: Tudor takes this if you're behind at 22:00.)

### 5.7 COULD
26. glTF airliner models (`ScenegraphLayer`, CC0/CC-BY, no liveries, credited); MapLibre terrain (Terrarium DEM); extruded buildings; replay scrubber from `events.jsonl`; button to start Sanju's fal debrief.

### 5.8 Submission assets (09:00–11:30)
27. README (screenshots of both views, setup, `.env.example`, architecture diagram, credits: VATSIM UK sector file GPL-3.0, OurAirports, OpenAP, OpenFreeMap, model licence); record the **backup demo video** on the seeded scenario (OBS, both screens); assemble the submission write-up with Tudor and Sanju's paragraphs; confirm the submission form fields.

## 6. Design specifics (non-negotiable look)
- **Controller scope:** EuroScope-like operational information hierarchy and density, with modern typography, spacing and restrained surfaces. Deep slate `#0E1417`, map lines `#2B3A40`, map labels `#4A5D63`, traffic `#E8EEF0`, unconcerned `#7C8B90`, selected `#4FD1E8`, transmitting `#7CF0B4`, STCA `#FF5A52`, wake `#F2B544`. JetBrains Mono (tags/data) + Inter (UI). Thin separators; no glow or gradients. Prioritise polished visuals while preserving recognizable controller workflow and readable data.
- **Viz:** graphite `#111518`/`#171C20`, hairlines `#2A3238`, signal amber `#F2B544`, selection cyan; tabular numerals; status chips.
- **Motion:** appear 180 ms fade + 4 px rise `cubic-bezier(0.2,0.8,0.2,1)`; panels 240 ms; camera 1.6–2.4 s; transmit pulse 1.2 s; STCA 2 Hz flash only.
- Use a small component kit (Radix primitives or hand-rolled) — no heavy UI framework theming fights.

## 7. Acceptance tests
- `test_contracts.py` and `smoke_test.py` green before every merge to `main`.
- Controller scope: EuroScope-like structure with a dominant 2D radar, arrival/departure lists, selected-aircraft details, METAR, transcript and alerts; selecting a flight list row and selecting a radar track stay synchronized. 40 synthetic aircraft at 60 fps (dev overlay FPS meter); pan/zoom anchored correctly; every layer toggles; tags never overlap after declutter in the demo scenario.
- Captions: partial → final transition visible; pilot words in sync with audio (±150 ms by ear).
- Two-device: radar on laptop A, viz on laptop B via hotspot — state, captions and room audio live; reconnect after killing viz Wi-Fi for 10 s.
- Demo console: each hotkey produces its beat within 2 s; judge mode legible from 3 m.
- Viz: chase cam follows a landing from 8 NM to touchdown without jitter (interpolated camera target).

## 8. Timeline & checkpoints
| Time | Deliver |
|---|---|
| 14:15–15:00 | Repo scaffold, server shell, wiring with stubs, radar dots, typed box |
| 15:00–16:00 | WS client + store + interpolation → **16:00 integration 1** |
| 16:00–19:00 | Radar layers, toggles, pan/zoom, tags, history/vectors, caption strip, PTT wiring to `AudioEngine` → **19:00 voice round trip on radar** |
| 19:00–22:00 | Side panels, session controls, tag states, event feed, `/viz` skeleton (map + silhouettes), session code + QR → **22:00 MVP** |
| 22:00–02:00 | Viz labels/stems/trails/glideslope, cameras + director's cut + chase cam, industrial UI, demo console + beat table; two-device test on venue Wi-Fi (by 23:00) → **02:00 wow tier** |
| 02:00–05:30 | Sleep |
| 05:30–09:00 | Report screen, polish pass (spacing, motion, empty states), COULD items → **09:00 freeze** |
| 09:00–10:30 | README, backup video, submission write-up |
| 10:30–11:30 | Rehearse on the real two-device setup, submit |

## 9. Overflow rule
You have the widest surface. The controller scope remains the primary operator interface and must keep its 2D radar plus core operational lists and controls. If behind, simplify the separate `/viz` audience view to a **2D** silhouette map (same deck.gl layers, pitch 0) and hand the report screen to Tudor. Cut order after that: glTF/terrain → departure cam/free orbit → declutter → decorative toolbar polish. Never cut: usable controller scope, arrival/departure list structure, caption strip, PTT, typed box, audience view with at least silhouettes + labels + follow-transmitting camera, demo hotkeys 1/4/8/J.

## 10. Definition of done
Both views run from one server, live on two devices (or extended display), styled per §6 with smooth motion; the seeded demo plays through all beats via hotkeys; report screen renders; README + backup video + submission done by 11:30.

## 11. Post-MVP technical-depth track (planned)
Do this only after the end-to-end MVP works. Keep `/` as the controller's 2D EuroScope-like workspace and `/viz` as the separate audience view. Add the following to the platform without implying that they are already implemented:
- Define additive optional contract shapes with Sanju and Tudor first (CONTRACTS §3.1); mirror every shared message in `server/contracts.py` and `web/src/protocol.ts` before wiring UI.
- Provide a per-transmission trace view that can show stage durations for capture, parsing, evaluation, response planning, composition, TTS, and delivery/playback. Show measured values and trace/session IDs; do not display invented latency.
- Add `/api/benchmarks` or an equivalent session report section for reproducible parser, speech, and end-to-end latency results, including sample count, build/model, hardware, and p50/p95/p99. Preserve the last 10 live latency samples as an explicitly separate short-window readout.
- Render `ConflictForecast` as a clearly labelled advisory probability and threshold, with seeded trajectory fan/spaghetti paths when available. Keep it distinct from deterministic STCA alerts and controller clearances.
- Add optional live-import controls and source/time/credit labels. Imported traffic is converted to simulated aircraft for the controller to work; it is never connected to or controlled through VATSIM or another live network.
- Use the ordinary UI test/build and contract validation gates for new UI and messages; agree exact performance limits with the owners before treating them as release gates.
