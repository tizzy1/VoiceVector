# SweatboxAI — Integration Contracts

> Read this before writing any code. It defines **who owns what, how modules call each other, and what goes over the wire**, so three people (and their AI agents) can build in parallel and plug together cleanly.
> Code is the source of truth: **`server/contracts.py`** (all Python models + interfaces) and **`web/src/protocol.ts`** (client-facing mirror). This doc explains them.

Team: **Tudor** (aviation: sim, pilot logic, rules, data) · **Sanju** (voice + LLM: STT, parser, composer, TTS, audio, captions, fal) · **Bhavin** (platform + front end: server shell, WS hub, radar, viz, design, controls, demo, report UI).

---

## 1. Repository layout & ownership

```
sweatbox-ai/
  CLAUDE.md                         shared agent context                         (Bhavin; everyone may propose)
  docs/                             PROJECT_REPORT, VOICE_PIPELINE, SIM_SPEC, CONTRACTS, ASSIGNMENT_*
  data/                             egkk.json, perf.json, telephony.json,        (Tudor)
                                    traffic.json, scenario_demo.json,
                                    readback_variants.json                       (Sanju generates, Tudor reviews)
  server/
    contracts.py                    SHARED — change only per §7                  (merge owner: Bhavin)
    main.py  hub.py  session.py     FastAPI app, WS hub, session + EventLog       (Bhavin)
    feed.py                         Event -> human feed line                      (Bhavin)
    sim/                            world.py (class World), aircraft.py,          (Tudor)
                                    kinematics.py, modes.py, procedures.py,
                                    traffic.py, runway.py, stca.py, registry.py,
                                    airspace.py, geo.py
    pilot/
      engine.py decision.py errors.py clearance.py                               (Tudor)
      readback.py validator.py variants_build.py                                 (Sanju)
    rules/                          engine.py (class RulesEngine), rules_*.py,     (Tudor)
                                    report.py
    voice/                          pipeline.py (class VoicePipeline),            (Sanju)
                                    stt_scribe.py, stt_fallback.py, normalise.py,
                                    numbers.py, parser/, tts.py, voices.py,
                                    frequency.py, metrics.py
    agents/                         llm.py, narrative.py, scenario.py,            (Sanju)
                                    fal_emergency.py, fal_debrief.py
    tests/                          tests/sim, tests/pilot, tests/rules (Tudor) · tests/voice (Sanju) · tests/integration (Bhavin)
  web/
    src/protocol.ts                 SHARED mirror of contracts                    (merge owner: Bhavin)
    src/audio/                      index.ts (AudioEngine), capture, player,      (Sanju)
                                    radio, mixer — NO React in here
    src/app/  src/radar/  src/viz/  src/ui/  src/report/                          (Bhavin)
```
**Rule:** edit only folders you own. Need a change elsewhere → ask the owner (or agree in chat and note it in the commit).

---

## 2. Runtime wiring (server, one process)

Built in `server/session.py` by Bhavin, exactly like this:
```python
bus      = EventLog()                                   # Bhavin, implements EventBus
world    = World(bus, DATA_DIR)                         # Tudor
bus.clock = lambda: world.t
pilot    = PilotEngine(world, bus, DATA_DIR)            # Tudor
rules    = RulesEngine(world, bus, DATA_DIR)            # Tudor
composer = Composer(DATA_DIR)                           # Sanju
voice    = VoicePipeline(world, pilot, composer, bus, hub)  # Sanju (reads API keys from env)
narrative = Narrative()                                 # Sanju

bus.subscribe(rules.on_event)
bus.subscribe(hub.on_event)                             # -> EventMsg to all clients (feed.py formats)
bus.subscribe(lambda e: (r := pilot.on_sim_event(e)) and spawn(voice.speak(r)))

async def sim_loop():                                   # every 1/sim_speed wall seconds, unless paused
    world.tick(1.0)
    pilot.tick(world.t)
    rules.on_tick(world.snapshot())
    await hub.send(world.public_state())
```
Constructor signatures above are **fixed**.

### 2.1 One controller transmission (owner of each step in brackets)
```
radar client  ── {"type":"ptt","state":"down"} + binary PCM frames ──►  hub [Bhavin]
hub           ── voice.on_ptt("down"), voice.on_audio_in(bytes) ──►      VoicePipeline [Sanju]
VoicePipeline ── Scribe partials ──► hub.send(CaptionMsg stage="partial")             (all clients)
radar client  ── {"type":"ptt","state":"up"} ──► voice.on_ptt("up")
VoicePipeline ── final text → normalise → parse(world.callsigns(), world.fixes()) → Transmission
              ── hub.send(CaptionMsg stage="final", chips, latency)
              ── if frequency blocked: bus.emit(ctrl_blocked); STOP (instruction not delivered)
              ── evals = world.evaluate(tx)                                           [Tudor]
              ── bus.emit(Event ctrl_transmission, data={tx, evals})  → rules + feed
              ── responses = pilot.handle_transmission(tx, evals)                     [Tudor]
              ── for r in responses: await voice.speak(r)
VoicePipeline.speak(r):
              ── rb = composer.compose(r.plan)                                        [Sanju]
              ── TTS stream starts immediately; release audio at max(first chunk, r.reaction_delay_s)
                 and when the frequency is free (reply scheduler)
              ── world.set_transmitting(cs, True); hub.send(AudioStartMsg, AudioChunkMsg…, AudioEndMsg)
              ── hub.send(CaptionMsg stage="pilot_text" then "pilot_words")
              ── on audio end: world.set_transmitting(cs, False)
                               pilot.on_readback_spoken(r, t_end)                     [Tudor]
                               world.schedule_apply(cs, r.apply, at=t_end + r.apply_delay_s)  [Tudor]
                               bus.emit(Event pilot_transmission, data={text, kind, plan})
```
Typed commands: `{"type":"typed","text":...}` → `voice.on_typed(text)` → same path from "final text" (no STT).

### 2.2 Pilot-initiated calls (check-in, requests, established, MAYDAY, re-calls)
`World.tick` emits `Event(type="pilot_call", callsign, data={"kind": ..., ...})` → the bus subscriber calls `pilot.on_sim_event(e)` → `PilotResponse` → `voice.speak(r)`. Re-calls after a block: `voice` calls `pilot.on_blocked(cs, tx_id)` → returns a `recall` response that voice schedules.

### 2.3 Controls (from either client)
`hub` receives `ControlMsg` → `session.handle_control()` [Bhavin] maps to:
| action | call |
|---|---|
| new_session / load_scenario | rebuild session; `world.load_scenario(name, seed)` |
| pause / resume / set_speed | session loop state; emit session events |
| set_traffic / set_runway | `world.configure(settings)` |
| set_error_rate | `pilot.set_error_rate(rate)` |
| inject | `world.inject(kind, callsign, params)` |
| force_error | `pilot.force_error(mode, kind, callsign)` |
| beat | demo beat table (PROJECT_REPORT §6.2) → the calls above (camera beats are client-side only) |
| reveal_errors | `voice.set_reveal_errors(on)` → voice adds `error_reveal` to captions |
| end_session | `rep = rules.report(id)` → `rep = await narrative.write(rep)` → `hub.send(ReportMsg)` |

---

## 3. Who emits which events
| Owner | Event types |
|---|---|
| Bhavin (session) | session_start, session_pause, session_resume, session_end, settings_changed |
| Sanju (voice) | ctrl_ptt_down, ctrl_ptt_up, ctrl_transmission, ctrl_blocked, pilot_transmission, pilot_blocked, overlap |
| Tudor (sim) | spawn, takeoff, landing, handoff, go_around, hold_entered, loc_captured, gs_captured, instruction_applied, level_reached, inject, pilot_call, alert_start, alert_end |
| Tudor (pilot) | error_planned, error_spoken, error_caught, error_missed |
| Tudor (rules) | finding |

`EventLog` also appends every event to `runs/<session_id>/events.jsonl` (replay + debugging).

### 3.1 Technical-depth event and data ownership (planned after MVP)
Implemented live-import boundary: `control/import_live` accepts only `{source: "demo" | "vatsim"}`.
The session fetches/normalizes server-side and calls Tudor's `World.import_live(snapshot)`.
`StateMsg` optionally carries `live_import_available` and `live_import: LiveImportStatus`.
Status includes phase, timestamps, attribution, complete counts and bounded skipped-detail samples.
Optional `AircraftPublic.traffic_source/source_time/fetched_at/source_attribution` preserve provenance;
all imported tracks subsequently move as simulated aircraft. No raw provider JSON goes over the socket.

| Data | Producer | Consumer |
|---|---|---|
| `TraceMsg` / `PipelineTrace` | Voice pipeline (Sanju) | Both views (Bhavin); final traces also append to `runs/<session>/traces.jsonl` |
| `Benchmarks` | Offline benchmark suite (Sanju, with Tudor's golden data) | `GET /api/benchmarks` and engineering overlay (Bhavin) |
| `ConflictForecast` | Simulator forecast engine (Tudor) | `StateMsg`, radar and viz overlays (Bhavin) |
| `LiveImportMsg` / `LiveImportStatus` | Session/fetcher (Bhavin) after `World.import_live` (Tudor) | Both views |

**Trace shapes (exact; additive, optional).** Bhavin adds these to `server/contracts.py` and mirrors them in `web/src/protocol.ts` (with `TraceMsg` added to `ServerMsg`) in one commit before Sanju emits traces:
```python
TraceCategory = Literal["capture", "stt", "language", "sim", "pilot", "voice", "playback"]

class TraceStage(BaseModel):
    name: str                      # VOICE_PIPELINE §20.1 stage table (exact names)
    category: TraceCategory
    t0_ms: float                   # relative to the anchor (PTT up, or compose start)
    t1_ms: float
    status: Literal["ok", "fallback", "error", "skipped"] = "ok"
    detail: dict[str, Any] = Field(default_factory=dict)

class PipelineTrace(BaseModel):
    tx_id: str
    speaker: Speaker               # "CTRL" or a callsign (pilot-initiated)
    reply_callsign: Optional[str] = None
    stages: list[TraceStage] = Field(default_factory=list)
    total_ms: float = 0.0          # anchor -> first pilot audio played
    final: bool = False            # False = provisional, stages still arriving

class TraceMsg(BaseModel):
    type: Literal["trace"] = "trace"
    trace: PipelineTrace
```

These are roadmap additions, not part of the current wire contract until merged. Before any owner implements them, Bhavin adds the optional/defaulted models and fields to `server/contracts.py` and mirrors the client-visible shapes in `web/src/protocol.ts` in the same commit. Forecast output is informational and never drives aircraft or replaces deterministic evaluation/STCA. Live source data is read-only input to the simulation; the app must not connect to or transmit commands on VATSIM or any real-world network.

### 3.2 Flight plans, editable lists and pronunciation (requested 15 Nov, not yet in the wire contract)
Additive and optional, added by Bhavin to `server/contracts.py` and `web/src/protocol.ts` in one commit before anyone implements them:
| Change | Where | Producer → consumer |
|---|---|---|
| `AircraftPublic`: `flight_rules`, `adep`, `ades`, `rfl` (FL number), `assigned_squawk`, `selected_level` (Mode S SFL = the aircraft's real cleared level), `strip: dict` (controller-edited list fields: `cfl`, `ahdg`, `aspd`, `dct`, `sts`, `clr`, `rwy`, `sid`/`star`) | `StateMsg` | World (Tudor) → lists and tags (Bhavin) |
| `ControlAction` `"edit_strip"`, params `{callsign, field, value}` → `World.edit_strip(callsign, field, value) -> bool` | client → server | Bhavin's lists → Tudor's World |
| `EventType` `"strip_edit"` (data `{field, value, by}`) | bus | World → feed, replay |
| `ReasonCode` `"QUERY_FIX_PASSED"` (direct to a fix already passed on the aircraft's own procedure) | `Evaluation` | World → PilotEngine → composer (Sanju adds the reply) |
| Inject params: `spawn_arrival` / `spawn_departure` accept `stage` (SIM_SPEC §1.9) | `ControlMsg` | session → World |
Auto-fill of CFL/AHDG/ASPD/DCT uses the controller's parsed `Transmission` (intended values), done inside World on `ctrl_transmission`; the readback never changes the strip. See SIM_SPEC §1.8, §1.9, §2.1.1, §5.5.

Planned optional shapes: trace stages carry an exact name/category, relative start/end milliseconds, status and detail; forecasts carry a callsign pair, estimated conflict probability, median CPA and sampled paths; imported-traffic status carries source, success, fetch time, imported/skipped counts and attribution. `AircraftPublic` and `StateMsg` may add source/risk/forecast fields, and session settings may select a traffic source. `import_live` is a control action routed to `World.import_live(targets, source, mode)`. The detailed target shape and acceptance thresholds live in the owner briefs and `SIM_SPEC.md`.

---

## 4. Network protocol

### 4.1 Endpoints (Bhavin)
| Route | Purpose |
|---|---|
| `GET /` | Radar app (built web, `role=radar`) |
| `GET /viz` | Visualisation app (`role=viz`) |
| `GET /report` | Report view |
| `GET /api/data/{name}` | Serves `data/{name}.json` (egkk, traffic, perf…) |
| `GET /api/session` | Current `SessionMsg` |
| `WS /ws?role=radar\|viz&session=CODE` | Everything live |

Server binds `0.0.0.0:8000`. In dev, Vite proxies `/api` and `/ws` to 8000.

### 4.2 Server → client (JSON, `type` field)
| type | When | Notes |
|---|---|---|
| `state` | every sim tick (1 Hz) | `StateMsg` — clients interpolate |
| `caption` | partial (~5/s while speaking), final, pilot_text, pilot_words | `CaptionMsg` |
| `audio_start` / `audio_chunk` / `audio_end` | pilot transmissions | released by the server at play time → clients play on arrival; base64 PCM16 (or mp3) |
| `event` | every bus event | `EventMsg` with `feed_text` |
| `report` | end of session | `ReportMsg` |
| `session` | on connect + on change | `SessionMsg` |

Audio goes to **all** clients; each client decides via `AudioEngine.setPlayback()` (radar: headset; viz: room speakers). Controller audio is **never** sent back to clients.

### 4.3 Client → server
JSON `ptt`, `typed`, `control` (any role may send `control`; only `radar` may send `ptt`/`typed`/binary). Binary frames = mic PCM16 mono 16 kHz LE, ~100 ms each, only between `ptt down` and `ptt up`.

---

## 5. Front-end audio boundary
`web/src/audio/index.ts` (Sanju) exports `createAudioEngine(): AudioEngine` implementing the interface at the bottom of `protocol.ts`. Stepped-on audio: a pilot stream with `blocked: true` or sharing an `overlap_group` is mixed with the heterodyne effect; an `event` message of type `overlap` with `data.with_ctrl = true` (controller keyed over a pilot) tells the engine to heterodyne the stream currently playing. Bhavin's app:
- creates it once, passes `sendBinary`/`sendJson` from the WS client;
- forwards every server message to `engine.handleServerMsg(m)`;
- binds Space (and the on-screen PTT button) to `pttDown()` / `pttUp()`;
- uses `onActivity` for tag pulses and `micLevel()` for the VU meter;
- uses `pilotWordClock(tx_id)` to reveal `pilot_words` captions in sync.
Sanju's module contains **no React** and no layout; Bhavin's UI contains **no Web Audio**.

---

## 6. First-hour stubs (so nobody blocks anybody)
| By | Owner | Stub (implements the real interface, minimal behaviour) |
|---|---|---|
| 15:00 | Bhavin | `main.py`, `hub.py`, `session.py` with the wiring in §2, `EventLog`, `/api/data`, WS; radar page drawing dots from `state`; typed box |
| 15:00 | Tudor | `World`: loads `scenario_demo.json`, flies straight lines, `evaluate` → all OK, `schedule_apply` handles heading/altitude/speed crudely, `public_state` complete (lat/lon included). `PilotEngine`: one `full` response per transmission, `plan.elements = tx.instructions`, `apply` mirrors them, no errors |
| 15:30 | Sanju | `VoicePipeline.on_typed` → regex for heading/altitude/speed → `Transmission`; `speak` → template text → `pilot_text` caption → `schedule_apply` (no audio yet). `Composer` with one template per type. `web/src/audio/index.ts` no-op implementation of `AudioEngine` |
| **16:00** | all | **Integration 1:** typed "BAW117 turn left heading 240" → readback caption → aircraft turns on the radar |

Later checkpoints: **19:00** voice round trip · **22:00** MVP (errors + corrections + captions) · **02:00** wow tier · **09:00** feature freeze (see each assignment).

---

## 7. Changing contracts
1. Additive optional fields with defaults: allowed. Post in chat, update `contracts.py` **and** `protocol.ts` in the same commit.
2. Renames, removals, type changes, new required fields: need agreement from the affected owners first.
3. Never change another owner's interface implementation to "make it work" — open a chat thread instead.
4. `tests/integration/test_contracts.py` (Bhavin) validates sample messages from each module against the models; run it before every merge to `main`.

## 8. Conventions
- Python 3.12, type hints, pydantic v2, `async` for I/O. TS strict, React function components.
- Lines < 100 chars. Small commits, `git pull --rebase` before pushing, merge to `main` at least every 2 h, keep `main` runnable.
- Secrets in `.env` (git-ignored): `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `FAL_KEY`.
- All randomness via a seeded `random.Random(settings.seed + salt)` — the demo must be reproducible.
- Every AI agent starts with: "Read CLAUDE.md, docs/CONTRACTS.md, server/contracts.py, and my assignment. Work only in my folders."
