# SweatboxAI — Plan v2 (Track 2: Voice, Vision & Generative Media)

> **One line:** A training simulator where trainee controllers talk to AI pilots on a shared frequency. The pilots read back, push back, step on each other, make mistakes on purpose, and fly the instructions on a real radar — with traffic generated continuously, arrivals and departures, at whatever intensity the mentor sets.

Supersedes `SWEATBOX_PLAN.md`. Written Sat 14 Nov 2026, 11:30.

---

## 0. What changed since v1

| Area | v1 | v2 |
|---|---|---|
| Track | Undecided | **Track 2: Voice, Vision & Generative Media** (sponsors: **fal**, **ElevenLabs**) |
| Bounties | — | Up to 2 sub-bounties: **Adaption** (+ one more if a fit appears) |
| Speech-to-text | ElevenLabs Scribe | **fal `wizper`** (Whisper v3 Large, fal-optimised); local faster-whisper fallback |
| Voices | ElevenLabs, fixed voice list | ElevenLabs **voice design** — pilot voices generated from persona descriptions |
| Traffic | 5 static aircraft | **Continuous generator**: arrivals + departures, single-runway mixed mode, separation-aware, difficulty ramp |
| Radio | One speaker at a time | **Stepped-on transmissions** (heterodyne squeal, blocked calls, re-calls) |
| Adaption | "AI adapts live" (wrong) | **Offline** parser fine-tune (Qwen3.5 2B/4B, LoRA) + retrain-on-misparses story |
| Data | STARs only | + **7 RNAV SIDs** for 26L, 69 fixes, `traffic.json` |

---

## 1. Track, sponsors, judging

- **Main track:** Voice, Vision & Generative Media. Judges will see many single-voice agents and image generators.
- **Our differentiator:** *five-plus AI speakers on one frequency, under a strict real-world protocol, where voice is the control interface to a live simulation — and the AI sometimes gets it wrong on purpose, because catching that is the training.*
- **Sponsor use must be central, not bolted on:**
  - **fal:** every trainee transmission goes through fal STT. Fallback TTS path also via fal (ElevenLabs/Kokoro/MiniMax hosted on fal).
  - **ElevenLabs:** every pilot voice, generated per aircraft via voice design; ATIS broadcast.
- **Sub-bounty (Adaption):** fine-tuned small ATC parser + measured latency/accuracy vs general LLM.

---

## 2. Team

| Person | Owns | Done by Sat 20:00 |
|---|---|---|
| **Tudor** | `server/sim/` (flight model, ILS, STCA), **traffic generator**, separation rules, data | Generator spawns arrivals + departures with no spawn conflicts; aircraft land and depart correctly |
| **A** | `web/` scope, tags, arrival/departure lists, difficulty slider | Full mouse control; both traffic flows visible |
| **B** | `server/voice/` fal STT, parser, readback formatter, ElevenLabs TTS, **audio mixer + stepped-on logic** | Speak → readback; two overlapping calls produce a squeal |
| **C** (if 4) | `server/agents/` voice design, ATIS, injects, debrief, Adaption run, pitch assets | Generated voices per aircraft; debrief renders |

With 3 people: C's work splits — voice design + ATIS → B; debrief + Adaption → Tudor (Sunday).

---

## 3. Accounts and keys (now)

| Service | Use | Status / action |
|---|---|---|
| **fal** | STT (`fal-ai/wizper`), fallback TTS | Get key; ask fal reps for credits |
| **ElevenLabs** | TTS (Flash v2.5), voice design | No free credits → buy **Starter (~$5–6)**; Creator if usage climbs. Cancel after. |
| Anthropic | Parser fallback, debrief, scenario-from-sentence | Key + ask for credits |
| **Adaption** | Dataset gen + AutoScientist | 1,250 credits. **Ask reps what that buys** before launching anything. |
| VATSIM feed | Optional live import | Server-side only, ≤ 1 poll / 15 s |

---

## 4. Dependencies (added since v1)

```txt
# server/requirements.txt  (v1 list plus:)
fal-client==1.0.3
```
```bash
cd web && npm i @fal-ai/client     # 1.11.0 — only if calling fal from the browser (prefer server-side)
```
Everything else unchanged from v1 §5 (FastAPI, uvicorn, pydantic, anthropic, elevenlabs, httpx, rapidfuzz, numpy, pytest; Vite + React + TS + zustand).

---

## 5. Data pack (`data/`)

| File | Contents |
|---|---|
| `egkk.json` | Runways, **69 fixes**, **16 STARs + 7 RNAV SIDs** (26L), CTR/CTA lines, centrelines, frequencies, 3° GS. Local NM projection, origin = 26L threshold. |
| `perf.json` | 10 types: climb/descent rates, speeds, ceiling, min clean, wake cat |
| `telephony.json` | 26 airlines → telephony, phonetic alphabet, digits |
| `traffic.json` | **New.** Arrival entry fixes (16) → STARs, SID list, Gatwick-ish fleet mix by airline/type, separation + wake tables, runway occupancy, difficulty presets |
| `scenario_demo.json` | Seed scenario (5 arrivals + injects) for a deterministic demo start |

**SIDs included (26L, RNAV):** BOGNA1X, HARDY1X, NOVMA1X, SFD1X, DAGGA1X, TIGER1X, WIZAD1X. Conventional "M" SIDs use DME points absent from the fix lists, so they're excluded.

**Still unverified:** STAR/SID altitude & speed constraints, hold details, ILS idents. Not needed for the demo.

---

## 6. Architecture

```
browser                          ⇄ WebSocket ⇄  FastAPI (single process)
  scope + tags + lists                            sim loop @ 1 Hz
  difficulty slider                               ├─ flight model + modes (STAR/SID/HDG/DCT/LOC/GS)
  push-to-talk                                    ├─ STCA + wake checks on final
  AUDIO MIXER (Web Audio)                         ├─ TRAFFIC GENERATOR (arrivals + departure queue)
   ├─ radio filter per stream                     ├─ RUNWAY MANAGER (single runway, mixed mode)
   ├─ overlap detection → heterodyne              └─ FREQUENCY MANAGER (who is transmitting, blocks)
   └─ squelch / hiss                              voice: fal STT → parser → readback → ElevenLabs TTS
  transcript + plain-English                      agents: voice design, ATIS, injects, debrief
```

**Rules unchanged:** the LLM never moves aircraft; contracts in `server/contracts.py` are the source of truth.

---

## 7. Contracts (changes from v1)

```python
class Aircraft(BaseModel):
    # ...all v1 fields, plus:
    flow: Literal["ARR", "DEP"]
    phase: Literal["QUEUED", "TAKEOFF", "SID", "STAR", "VECTORS", "FINAL", "LANDED", "HANDED_OFF"]
    route_name: str | None          # STAR or SID name
    route_idx: int                  # next fix index
    voice_id: str | None            # generated ElevenLabs voice
    persona: str

class FrequencyState(BaseModel):
    transmitting: list[str]         # callsigns (or "CTRL") currently keyed
    blocked_until: float | None

# New Event types: spawn_arr, spawn_dep, takeoff, landing, handoff,
#   wake_violation, blocked_transmission, blocked_resolved, recall
```

New WS messages:
- client → server: `{type:"set_traffic", preset | arr_per_hr, dep_per_hr}`, `{type:"ptt", state:"down"|"up"}`
- server → client: `{type:"radio", ..., overlaps:[callsign,...]}` so the mixer knows to squeal

---

## 8. Sim core

Unchanged from v1 §9 (kinematics, bank-limited turns, modes, ILS capture, STCA, unable rules, behaviour tests). **Additions:**

- **SID mode:** after takeoff, fly runway heading to 1,500 ft (or first fix), then follow SID fixes; climb to `departure_initial_alt_ft` (4,000) and level off — the controller must climb them further.
- **Handoff:** departures leaving ~25 NM or above FL70 after being told "contact London" → `HANDED_OFF`, removed. Arrivals on `contact tower` inside 4 NM → `LANDED` at threshold.
- **Behaviour tests to add:**
  7. Departure follows SID, levels at 4,000 if not climbed.
  8. Generator never spawns two arrivals < `arrival_spawn_min_spacing_nm` apart at the same entry.
  9. Departure not released when next arrival is < `departure_release_gap_nm` from threshold.
  10. Medium departure behind Heavy is held ≥ 2 min.

**Realism note:** at real Gatwick, departures go to London TC, not Gatwick Director. A combined position is a sim simplification — and it adds workload, which is the point of training. Say so if asked.

---

## 9. Traffic generator (Tudor) — the new core

### Goals
1. Continuous, never-ending traffic at a configurable rate.
2. Every spawn is **legal at birth** (no instant conflicts) and **solvable**.
3. Realistic mix: airlines, types, entry points, SIDs.
4. Single-runway mixed-mode logic, like real Gatwick.

### Arrivals
Every tick, accumulate `arr_per_hr / 3600`; when ≥ 1, try a spawn:
1. Pick entry fix (weighted, `traffic.json`), then a STAR starting there.
2. Pick airline (weighted) → type (weighted) → callsign `AAA####` (unique on scope).
3. **Spacing check:** no aircraft within `arrival_spawn_min_spacing_nm` (10) of the entry fix at any level → else defer to next tick (don't drop; keep a backlog).
4. **Level assignment:** pick from `entry_fl_range`; ensure ≥ 1,000 ft from any aircraft within 10 NM. If none free, defer.
5. **Feasibility probe:** project the new aircraft along its STAR for 3 min against everyone else's projected path; if a loss of separation is unavoidable without intervention in < 60 s, defer.
6. Spawn → generate voice (async, see §12) → pilot checks in: "Gatwick Director, Easy four five alpha bravo, flight level one one zero, information Kilo."

### Departures
- **Queue** at holding point; spawn into the queue at `dep_per_hr`, with a SID chosen (weighted) and callsign/type as above.
- **Release conditions** (runway manager), all must hold:
  - Runway free (last arrival landed + `runway_occupancy_s.arrival`, last departure + occupancy).
  - Next arrival ≥ `departure_release_gap_nm` (6 NM) from threshold.
  - **Departure time separation** from previous departure:
    - same initial route: 2 min
    - routes diverging ≥ 45°: 1 min
    - **wake:** Medium/Light behind Heavy 2 min; Light behind Medium 2 min.
- On release: `TAKEOFF` → accelerate on runway, rotate at Vr (~145 kt), climb → `SID`. Pilot calls: "Gatwick Director, Speedbird two one five, passing two thousand climbing four thousand, BOGNA one X."
- **Optional (if time):** the trainee issues the release ("Speedbird 215, cleared for take-off" → actually tower's job; for the sim, a "Release" button on the departure list instead).

### Difficulty
- Presets in `traffic.json`: easy 10/8, medium 18/14, busy 26/20 (arr/dep per hour).
- **Ramp** preset: easy → busy over 30 min — how real sweatbox exercises build.
- Slider in the UI; instructor can change mid-session.
- Deferred spawns form a visible backlog; if it grows, the generator is exceeding what the airspace accepts — show it ("4 arrivals holding at entry").

### Determinism for the demo
- Seeded RNG. `scenario_demo.json` + seed → identical run every time. Rehearse on that seed.

---

## 10. Separation rules ⚠️ Tudor to verify

`traffic.json` uses **ICAO PANS-ATM defaults**. UK (CAP 493) uses its own wake categories and Gatwick may apply RECAT-EU. Tudor: check and edit the JSON before the demo — you know these better than any default.

| Rule | Default |
|---|---|
| Radar minimum | 3 NM / 1,000 ft |
| Final approach wake (distance) | H→H 4, H→M 5, H→L 6, M→L 5 NM (else 3) |
| Departure wake (time) | H→M 2, H→L 2, M→L 2 min |
| Departure same route / diverging ≥ 45° | 2 / 1 min |
| Runway occupancy | arr 55 s, dep 45 s |

**Wake checks on final** become a new STCA rule: if the follower is inside the wake minimum for the leader's category → amber alert + `wake_violation` event. Great debrief material.

---

## 11. Stepped-on transmissions (B) — new

### When it happens
1. **Pilot–pilot overlap:** two pilots due to transmit within ~800 ms of each other. Happens naturally at high traffic; also injectable.
2. **Controller blocks a pilot:** trainee keys PTT while a pilot is transmitting (or vice versa).
3. **Scripted inject** for the demo (guaranteed moment).

### What the trainee hears
- Both voices through their radio filters, **summed**, plus a **heterodyne tone** (two carriers beating), heavy clipping. Unintelligible.
- Web Audio graph per overlap:
  ```
  voiceA → bandpass(300–3000) ┐
  voiceB → bandpass(300–3000) ┼→ sum → waveshaper(hard clip) → out
  OscillatorNode(~1.1 kHz) ×  │
   GainNode modulated by       │
   OscillatorNode(~7 Hz LFO) ──┘
  ```
  Vary the tone frequency slightly per event so it doesn't sound canned.

### Server logic (frequency manager)
- Track `transmitting` set. When a second speaker keys while one is active → mark both transmissions **blocked**.
- Blocked pilot transmissions: **not delivered** to the parser/state; pilot **re-calls** after 3–6 s (randomised, so they don't collide again — or occasionally do, at high traffic).
- If the **controller's** transmission is blocked: the instruction is **not applied**. The pilot either says nothing or "station blocked, say again". The trainee must notice and repeat — exactly the real failure mode.
- Pilots never transmit while the controller's PTT is held (they "hear" the carrier) — except in the deliberate overlap case.

### Correct trainee responses (graded in debrief)
- "Station(s) calling, say again" or "Blocked, say again" → `blocked_resolved` ✓
- Calling one of them by callsign ("Speedbird 117, say again") → ✓
- Ignoring it → after 15 s, `blocked_unresolved` ✗ (and the pilot may re-call anyway)
- Assuming an instruction was received when it was blocked → aircraft doesn't comply; logged ✗

### Demo moment
At busy traffic, two pilots check in at once → squeal → trainee: "Two stations blocked, say again" → they re-call one at a time.

---

## 12. Voice pipeline (B)

```
PTT down/up → MediaRecorder → server → fal wizper → normalise → regex → (Claude fallback)
  → Transmission → frequency manager → sim → RadioCall → readback formatter
  → ElevenLabs Flash v2.5 (aircraft's generated voice) → browser mixer → radio filter
```

- **fal STT:** `fal-ai/wizper` (fast Whisper v3 Large). Upload the clip, pass `language="en"`. Bias/normalise callsigns and fixes after transcription (fuzzy match against scope). Fallback: local faster-whisper with an `initial_prompt` of callsigns + fixes.
- **Latency budget:** < 2 s PTT-release → readback start. Display the measured number.
- **Readback formatter:** deterministic (v1 §10). LLM never writes numbers.
- **Clip cache:** stock phrases per voice ("say again", "standby", "wilco", "unable") cached at voice-creation time.

---

## 13. Generative media (C or B)

1. **Generated pilot voices (ElevenLabs voice design).** On spawn, build a persona description from airline + random traits: "early-40s Turkish captain, calm, light accent" / "young British first officer, quick, slightly nervous". Generate a voice; store `voice_id` on the aircraft. Pre-generate a pool of ~15 at startup so spawns don't wait; generate more in the background.
2. **Scenario from a sentence (Claude + generator).** Mentor types: "busy Friday evening, thunderstorms south-east, one emergency" → Claude maps to generator params (rates, which entries weighted, closed STARs, inject schedule, wind/QNH) → generator runs it.
3. **Generated ATIS (ElevenLabs).** Build the ATIS text deterministically (runway, wind, visibility, QNH, info letter), speak it with a fixed "ATIS" voice, loop it on a side channel. Pilots quote the information letter on check-in; change it mid-session and pilots on old info get "information Lima now current" — real procedure.
4. *(Stretch)* Debrief read aloud in a mentor voice.

---

## 14. Agents (C)

| Agent | Model | Job |
|---|---|---|
| Parser fallback | claude-haiku-5-5 | Free text → `Transmission`, `ambiguities` not guesses |
| Scenario builder | claude-sonnet-5-5 | Sentence → generator params |
| Instructor | rules + Haiku | Injects: MAYDAY, go-around, radio failure, forced overlap, readback errors, wrong-callsign readback |
| Debrief | claude-sonnet-5-5 | Event log → report: LoS, wake violations, blocked calls handled, readback errors caught/missed, departures released vs delayed, score |

Visible agent log panel stays mandatory.

---

## 15. Adaption sub-bounty (Sunday, parallel)

**What it is:** offline dataset generation + fine-tuning. The output model is frozen; improvement = new data + new run. Not live adaptation.

**Plan:**
1. Invent a Dataset → ~1–2k rows: ATC transmission → `Transmission` JSON, seeded with our telephony, fixes, SIDs/STARs, CAP 413 patterns, plus messy variants (hesitations, wrong order, missing words).
2. AutoScientist: **Qwen3.5 2B or 4B, LoRA SFT.** Small = fast = fits the radio latency budget.
3. Measure on a held-out set: **adapted model vs Claude Haiku vs regex** — accuracy and latency. That table is the bounty pitch.
4. Story: "Every 'say again' and every misparse becomes a training row; the parser is retrained on what it misheard." Honest cadence: between sessions.

**Credits (1,250):** unknown conversion → ask reps first. Small dataset first, smallest model, LoRA, ≤ 2 runs, skip augment/translate/localise. **Never a dependency** — regex + Claude ships regardless.

---

## 16. UI additions (A)

- **Arrival list:** callsign, type, wake, entry, STAR, level, distance to threshold, sequence #.
- **Departure list:** queue order, SID, type, wake, earliest release time (from separation rules), "Release" button.
- **Traffic control:** preset buttons + arr/dep sliders; backlog counter.
- **Frequency indicator:** shows who is transmitting; flashes red on overlap ("BLOCKED").
- **Wake alerts** on final (amber).
- **ATIS panel:** current letter + play button.

---

## 17. Timeline (from now)

| Time | Goal | Checkpoint |
|---|---|---|
| 11:30–12:30 | Repo, contracts v2, stubs; Tudor: generator skeleton; B: fal STT hello-world | Contracts agreed |
| 12:30–16:00 | Sim core + **arrival spawner**; scope + lists; fal → parser → ElevenLabs readback | **16:00:** arrivals spawn and fly STARs; voice round-trip works |
| 16:00–20:00 | **Departures + runway manager**; ILS + STCA; **audio mixer + stepped-on**; tag menus | **20:00:** both flows, voice in/out, overlaps squeal |
| 20:00–23:00 | Wake checks, difficulty ramp, voice design pool, ATIS, injects | **23:00:** push, decide cuts |
| 23:00–06:00 | Sleep in shifts | |
| Sun 07:00–09:00 | Debrief, scenario-from-sentence, Adaption run, seeded demo scenario | **09:00 feature freeze** |
| 09:00–11:00 | Polish, latency numbers, README, Adaption comparison table | |
| 11:00–12:00 | Record backup video on fixed seed; rehearse 3× | Submit early |

### Cut order if behind
1. Scenario-from-sentence
2. ATIS
3. Adaption run
4. VATSIM import
5. Voice design (fall back to fixed voice list)
6. Departure SIDs beyond 3 (keep BOGNA1X, NOVMA1X, SFD1X)

**Never cut:** voice round-trip, arrivals generator, ILS landing, STCA, stepped-on transmissions, mouse control.

---

## 18. Demo script (3 min)

1. **(20 s)** Problem + credibility: student pilot, ran an ATC academy; sweatboxes need rooms of humans playing pilots.
2. **(20 s)** Start "ramp" preset. Arrivals appear at real entry points, departures queue. "Traffic never stops — the generator spawns it legally separated, at whatever intensity the mentor sets."
3. **(50 s)** Vector an arrival onto the ILS by voice; release a departure into the gap; plain-English line under each call.
4. **(20 s)** **Stepped-on moment:** two pilots check in at once → squeal → "Two stations blocked, say again."
5. **(15 s)** "Unable" moment (speed too low at altitude).
6. **(15 s)** Readback error caught.
7. **(20 s)** Judge takes the PTT for one instruction.
8. **(20 s)** Debrief: wake violations, blocked calls handled, readback errors, latency. One line on Adaption: "parser fine-tuned on Qwen 2B — X% accurate at Y ms vs Z ms for a general LLM."

Backup: recorded video on the fixed seed.

---

## 19. Judge Q&A additions

- **"How do you stop the generator creating impossible situations?"** Spawn-time spacing check, level assignment against neighbours, and a 3-minute feasibility probe — deferred spawns go to a visible backlog rather than being forced in.
- **"Why would two pilots transmit at once?"** Because they do, constantly, at busy units — it's one of the first things trainees struggle with, and existing sims can't reproduce it without two humans.
- **"Is the separation real?"** ICAO-based minima with UK wake categories (after Tudor's edit), checked every tick; wake violations on final are logged.
- **"What does fal do?"** Every word the trainee says goes through fal's Whisper. **"ElevenLabs?"** Every pilot has a voice generated for them, plus the ATIS.

---

## 20. Risks (new)

| Risk | Mitigation |
|---|---|
| Generator floods the scope | Spacing + feasibility checks, backlog, rate caps |
| Overlap logic feels random/unfair | Rate scales with traffic; deterministic seed for demo; inject for the guaranteed moment |
| Voice design latency on spawn | Pre-generated pool; background top-up; fixed-voice fallback |
| fal/ElevenLabs credits run out | Local faster-whisper; fal-hosted TTS; browser speechSynthesis |
| Wrong separation values embarrass us | Tudor verifies `traffic.json` before freeze |
| Two traffic flows double integration work | Arrivals first (16:00), departures second (20:00) — departures are cuttable to 3 SIDs |

## 21. Technical-depth upgrade (post-MVP)

The technical-depth proposal extends the existing product after the complete MVP works; it does not change the MVP priority or merge the controller scope with the separate audience demo. The planned additions are a per-transmission voice-pipeline trace, repeatable parser/STT/latency benchmarks, a seeded probabilistic conflict forecast, and optional read-only live traffic snapshots normalized into simulated AI-pilot aircraft. Forecasts remain advisory and separate from STCA. Live imports never transmit to or control VATSIM or real aircraft. See `PROJECT_REPORT.md` §15 and `SIM_SPEC.md` §§3.6–3.7 for the maintained specification and acceptance approach. This plan is historical and its original hackathon deadlines are not refreshed by this addition.
