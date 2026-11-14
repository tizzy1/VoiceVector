# SweatboxAI — Project Report

**Imperial AI Hackathon · Track 2: Voice, Vision & Generative Media**
**Sub-bounties targeted: Best use of ElevenLabs (primary) · Best use of fal (secondary)**
**Deadline: submit by Sun 15 Nov, 11:30 (hard stop 12:00). Written Sat 14 Nov, 14:00.**

> **One line:** An AI radar simulator where trainee air traffic controllers talk to AI pilots on a live frequency. The pilots listen in real time, read back in their own generated voices, sometimes get it wrong on purpose, and fly the instructions on a real Gatwick radar — while a second screen shows the whole thing as a cinematic 3D picture anyone can follow.

This document supersedes `SWEATBOX_PLAN.md` and `SWEATBOX_PLAN_V2.md`. Detailed specs:
| Doc | Covers |
|---|---|
| `VOICE_PIPELINE.md` | PTT → STT → parse → pilot → errors → readback → TTS → corrections, latency |
| `SIM_SPEC.md` | Aircraft behaviour, instruction catalogue, legality rules + session report, airspace, radar display, sim↔voice integration |
| `data/` | `egkk.json` v2 (full airspace), `perf.json`, `telephony.json`, `traffic.json`, `scenario_demo.json` |

---

## 1. Why this wins

### The problem (say it in one breath)
Every trainee controller needs hundreds of hours in a simulator ("sweatbox"), and every session needs a room of humans pretending to be pilots. Mentors and pseudo-pilots are the bottleneck — and they can't reliably make the *realistic mistakes* trainees most need to catch.

### Credibility
Tudor is a student pilot, founded and ran an ATC training academy (ATC24 Academy), and built TrackArc (flight-route optimisation). This is the tool he wished his trainees had.

### What judges will see that no other team has
1. **A real conversation on a real frequency.** Speak into a headset; within ~1–2 s a pilot answers in a distinct generated voice, through radio crackle, and the aircraft visibly turns.
2. **The AI listening live.** Your words appear on screen *as you speak*; the pilot's words appear *as they speak*, word by word.
3. **Pilots that get it wrong on purpose.** A pilot reads back 5,000 instead of 4,000 — or reads back "left" and turns right. The trainee catches it, corrects it, and the pilot repeats only the corrected part.
4. **Two pilots talk at once.** A squeal; neither is readable. "Two stations blocked, say again." Nobody else will have this.
5. **A cinematic second screen.** The same live session as a tilted 3D map of Sussex with aircraft at their real heights, a chase camera sliding down the glideslope into Gatwick, and plain-English captions — so non-pilots understand everything.
6. **A judge takes the mic** and controls an aircraft themselves.
7. **An end-of-session report** that knows what was legal and what wasn't, with a score, timeline and replay — and an AI instructor you can talk to about it.

### Track fit (Voice, Vision & Generative Media)
| Leg | How |
|---|---|
| Voice | Real-time multi-speaker voice agents on one frequency, PTT discipline, live captions, stepped-on transmissions |
| Vision | Radar + live 3D visualisation, driven by the same state; plain-English visual explanation |
| Generative media | Pilot voices generated per aircraft (voice design), generated ATIS broadcast, scenario generated from a sentence, generated debrief narrative |

### Pitch line for ElevenLabs ("real-time voice agents beyond plain TTS")
> Not one assistant reading text — five-plus voice agents sharing a frequency, each listening through ElevenLabs Scribe Realtime, reasoning against a live simulation, and answering in a voice ElevenLabs designed for it, under a strict real-world radio protocol.

---

## 2. Sponsor usage

### ElevenLabs (primary sub-bounty) — central, five products
| Product | Use | Visible in demo |
|---|---|---|
| **Scribe v2 Realtime** | Controller speech streamed while speaking; **manual commit on PTT release**; keyterms (callsigns, fixes, "niner") | Live caption strip filling in as you talk |
| **Flash v2.5 TTS (streaming)** | Every pilot transmission; first audio in a few hundred ms | Pilots answering fast |
| **Voice Design** | A unique voice generated per aircraft from a persona ("calm Turkish captain", "nervous student") | Every pilot sounds different |
| **TTS with timestamps** ⚠ verify | Word-synced pilot captions + tag pulse timed to speech | Pilot captions appear word by word |
| **ATIS** (TTS, fixed voice) | Generated airport information broadcast, looped | Play button; pilots quote the letter |
| *(could)* **Sound effects** | Control-room ambience / squelch textures | Subtle |

### fal (secondary — use only where it genuinely fits)
| Product | Use | Why it's a real fit |
|---|---|---|
| **Chatterbox TTS (Resemble on fal)** with emotion control | MAYDAY/PAN calls in a genuinely stressed voice | Calm TTS reading "MAYDAY" breaks the illusion; emotion control fixes exactly that |
| **PersonaPlex** (full-duplex speech-to-speech) | Post-session spoken debrief with an AI instructor, seeded with the session report | Free conversation, nothing safety-critical — what full-duplex is for |
| **Wizper** | Fallback STT if Scribe fails | Resilience |

If time runs short, fal features are the first sponsor items cut (§9). Don't force them.

### Anthropic (Claude) — the reasoning layer
Haiku 5.5: parser fallback · Sonnet 5.5: debrief narrative, scenario-from-sentence · at startup: generates the readback variant bank. Claude never moves aircraft, decides legality or chooses errors.

### Dropped
Adaption fine-tune (sub-bounty slots go to ElevenLabs + fal). Autonomous-Agents-track bounties (Synthesia, MongoDB, etc.) — not our track.

---

## 3. Product overview

### 3.1 Two views, one live session
| View | Device | Audience | Purpose |
|---|---|---|---|
| **Controller scope (operator)** | Controller laptop + headset | The trainee | EuroScope-like operational layout and information density, restyled as modern professional software; radar, flight lists, METAR, communications and controls |
| **Live visualisation (audience)** | Separate device → projector | Judges / audience | Independent presentation view of the same live session; industrial control-room styling, potentially 3D, with aircraft, camera modes, plain-English captions and room audio |

These are separate interfaces for different jobs. The controller scope prioritises realistic ATC workflow and fast access to operational information. The audience visualisation prioritises explaining the live session and may use a stylised 3D scene. Do not turn the controller scope into the 3D demo view.

### 3.2 Two-device mode — feasible, include it (SHOULD)
The server already broadcasts state over WebSocket, so a second device is just another subscriber.
- Server runs on the controller laptop, bound to `0.0.0.0:8000`. Second device opens `http://<laptop-ip>:8000/viz?session=XXXX` (QR code shown in the radar's session panel).
- **Audio routing:** the controller wears a headset (mic + ear). The **viz device plays the pilot radio audio to the room** (server broadcasts the same audio chunks), and shows both caption streams. Controller audio is never replayed → no feedback loop; PTT gating means the mic is live only while pressed anyway.
- **Network risk:** venue Wi-Fi often blocks device-to-device traffic. Fallbacks in order: phone hotspot (both devices on it) → one laptop with the viz window on the HDMI-extended display → tunnel (cloudflared) if internet is fine.
- Effort: ~1–2 h (session codes, `/viz` route, audio fan-out, QR). Test on venue Wi-Fi **Saturday evening**.

### 3.3 Feature list by tier
**MUST (demo exists without it? no)**
- Sim: arrivals on STARs, vectors, altitude/speed, ILS capture + landing, continuous arrival generation, STCA (loss of separation)
- Controller scope: EuroScope-like operational layout with map + airspace layers, data tags, history, speed vectors, pan/zoom, smooth 60 fps interpolation, arrival/departure lists, selected-aircraft details, METAR/weather and communications views
- Voice: PTT → Scribe Realtime → parser → pilot → template readback → ElevenLabs streaming TTS → radio filter
- **Live captions** (controller partials; pilot text)
- Readback variation (template engine) + validator
- Planned readback errors (consistent mode) + **corrections with partial readback**
- Typed command box (voice fallback)
- Seeded demo scenario

**SHOULD (the wow — target by Sunday 08:00)**
- **Visualisation view (3D)** with chase cam on final, plain-English labels, speech bubbles
- **Two-device mode**
- **Stepped-on transmissions** (heterodyne)
- **Generated pilot voices** (voice design pool)
- **Demo console** (presenter beat triggers, §6)
- **Session report** (core rules) with score + timeline
- Departures on 3 SIDs + runway manager
- Execution errors (wrong turn / wrong level)
- Latency readout
- Word-synced pilot captions

**COULD (stretch, Sunday 07:00–10:00)**
- fal Chatterbox stressed MAYDAY · fal PersonaPlex spoken debrief
- Generated ATIS · scenario-from-sentence
- Full rule catalogue · replay with audio
- VATSIM "tonight's real traffic" import · easterly runway config · 3D aircraft models (vs silhouettes) · terrain

---

## 4. Architecture

```
 CONTROLLER LAPTOP                                         SECOND DEVICE (projector)
 ┌───────────────────────────┐                             ┌──────────────────────────┐
 │ Radar UI (React + Canvas) │                             │ Viz UI (MapLibre+deck.gl)│
 │ PTT · captions · controls │                             │ captions · room audio    │
 └──────────┬────────────────┘                             └────────────┬─────────────┘
            │ WS (state, audio, captions, events)                       │ WS (subscribe)
 ┌──────────▼──────────────────────────────────────────────────────────▼─────────────┐
 │ FastAPI server (one process, in-memory)                                           │
 │  sim loop 1 Hz ─ traffic generator ─ runway mgr ─ STCA ─ rules engine ─ event log │
 │  frequency manager / reply scheduler                                              │
 │  voice: Scribe RT client → normalise → parser (registry) → pilot decision         │
 │         → error policy → readback composer → validator → ElevenLabs TTS stream    │
 │  agents: Claude (fallback parse, report narrative, scenarios, variant bank)        │
 │  fal: Chatterbox (emergency voice), PersonaPlex (debrief), Wizper (fallback)       │
 └───────────────────────────────────────────────────────────────────────────────────┘
```
Design rules: the LLM never moves aircraft · one instruction registry drives parser, sim, readbacks and rules · everything is an event (report and replay come free).

### Tech stack
**Backend:** Python 3.12+, FastAPI, uvicorn[standard], pydantic, anthropic, elevenlabs, fal-client, httpx (HTTP/2), rapidfuzz, numpy, pytest.
**Frontend:** Vite + React + TypeScript, zustand, Canvas 2D (radar), MapLibre GL + deck.gl (viz), AudioWorklet + Web Audio (capture, radio filter, mixer), Framer Motion (UI transitions).
**Map tiles:** OpenFreeMap (no key) — verify at venue; fallback: plain dark background with the coastline from data.
**3D models:** generic CC0/CC-BY airliner glTF, no liveries, credited.

---

## 5. Design system & styling

### 5.1 Controller scope — "modern EuroScope"
Preserve the familiar operational structure and information density of a professional ATC workstation, while replacing legacy visual styling with a modern, clear interface. The controller scope is a practical radar tool, not the audience's 3D presentation.
- Layout: persistent top status/toolbar; large central 2D radar; operational side or lower panes for arrivals, departures, selected-flight details, communications/transcript, event/alert feed and METAR. Keep the radar dominant and let panes resize or collapse.
- Arrival and departure lists should behave like flight strips. Show callsign, aircraft type, wake category, route/procedure, current level, cleared level, speed, heading/assigned speed when present, phase/status, and sequencing or release information when available. Selecting a row selects the same aircraft on the radar; selecting a track focuses its row and detail view.
- Make assigned clearance and observed state easy to compare. Keep alerts, frequency/transmission state, runway, session time and weather visible without obscuring traffic.
- Core controls should work: select aircraft, pan/zoom/range, layer toggles, list filtering/sorting, transcript access, typed transmission and PTT when available. Secondary controls may be staged visually, but must not look like active controls if they do nothing.
- Include a METAR panel for EGKK with observation time, wind, visibility, cloud, temperature/dew point, QNH and raw report. Clearly indicate stale/unavailable data; never present placeholder weather as live METAR.
- Background deep slate `#0E1417`; map low-contrast (`#2B3A40` lines, `#4A5D63` labels); traffic brightest (`#E8EEF0`). Accents sparingly: selected cyan `#4FD1E8`, transmitting pulse `#7CF0B4`, STCA red `#FF5A52`, wake/caution amber `#F2B544`.
- Type: **JetBrains Mono** for tags/data, **Inter** for UI. Fine separators and restrained borders, no glow or gradients. Prioritise visual hierarchy and polish while keeping the operational information authentic and legible.
- Track/data block, map layers and radar interaction: `SIM_SPEC.md` §5.

### 5.2 Live audience visualisation — "industrial control room"
Think premium operations software (SCADA / mission control), not a game.
- Graphite surfaces `#111518` / `#171C20`, hairline borders `#2A3238`, a single warm signal colour (amber `#F2B544`) plus cyan for selection; status chips with tabular numerals.
- Layout: full-bleed map or stylised 3D scene; slim top status bar (session clock, traffic count, active runway, latency, LIVE indicator); left rail = flight strip cards (arrivals/departures); bottom = caption strip; right = event feed ("BAW117 cleared ILS 26L", "Readback error caught ✓"). This is a separate audience view and does not replace the controller's 2D scope or its operational panels.
- **Plain-English everywhere:** "British Airways 117 · descending to 4,000 ft · 210 kt".
- Aircraft: silhouettes (MUST for viz) → glTF models (COULD); colour by flow (arrivals cool, departures warm); altitude stems to the ground; fading trails at true height; translucent glideslope ramp into 26L; procedure lines in the air.

### 5.3 Motion (both views)
| Element | Motion |
|---|---|
| Aircraft | Interpolated 60 fps from 1 Hz state (render at t−1 s, lerp position + slerp heading) |
| Tags / cards appearing | 180 ms fade + 4 px rise, `cubic-bezier(0.2, 0.8, 0.2, 1)` |
| Panels | 240 ms slide/fade |
| Camera moves (viz) | `flyTo` 1.6–2.4 s ease-in-out; never cut |
| Transmitting aircraft | Soft 1.2 s pulse ring, synced to audio start/end |
| Captions | Controller: partial text in muted tone → solidifies on commit, parsed values highlight in accent. Pilot: word-by-word reveal from timestamps |
| Alerts | 2 Hz flash for STCA only; everything else steady |
| Respect `prefers-reduced-motion` |

### 5.4 Caption strip (bottom, both views)
```
CTRL  ▍ speedbird one one seven turn left heading two four zero descend altitude four thousand
      → BAW117 · HDG 240 L · ALT 4000                                     [parsed 0.21 s]
BAW117 ▍ Left two four zero, descending four thousand, Speedbird one one seven   [1.3 s]
```
Readback errors are **not** highlighted in the caption (the trainee must catch them); the event feed marks them after they're caught or missed. A demo toggle ("Reveal errors") highlights them for judges.

---

## 6. Controls

### 6.1 Session controls (radar, collapsible panel)
New session · scenario (Demo / Ramp / Busy / Custom) · runway (26L; 08R COULD) · traffic preset + arrival/departure sliders · error rate (Off / Low / Med / High) · personas mix · pause / resume · sim speed (1× / 2×) · instructor injects (MAYDAY, go-around, radio failure, force readback error, force execution error, force stepped-on) · layer toggles · end session → report · second-screen QR + session code.

### 6.2 Demo mode controls (viz, plus hotkeys on either device)
- **Camera:** Overview · Follow transmitting aircraft (auto "director's cut") · Chase cam on final · Departure cam · Free orbit.
- **Overlays:** plain-English labels · procedures · airspace · altitude stems · "Reveal errors".
- **Presenter beats (hotkeys, deterministic):**
  | Key | Beat |
  |---|---|
  | `1` | Load seeded demo scenario |
  | `2` | Spawn the conflict pair |
  | `3` | Force a stepped-on check-in |
  | `4` | Force readback error on the next instruction |
  | `5` | Force wrong-turn execution error |
  | `6` | MAYDAY inject (stressed voice) |
  | `7` | Chase cam on the next landing |
  | `8` | End session → report |
  | `J` | Judge mode: big on-screen PTT, larger captions |
- Beats make the demo repeatable regardless of randomness.

---

## 7. Feature specs (summary — detail in the linked docs)

### 7.1 Voice pipeline (`VOICE_PIPELINE.md`)
- Order: PTT → Scribe Realtime (streamed, manual commit) → normalise → parse (regex; Claude Haiku fallback) → resolve callsign → pilot decision (comply / unable / query) → **error policy** (code, seeded) → readback composer (pre-generated variant bank) → **validator** (re-parses the readback; no unplanned errors ever) → ElevenLabs Flash stream → playback at `max(audio ready, persona reaction delay)` → sim applies the flown values.
- Machine latency target ≤ 0.8 s p50 (standard phraseology); perceived 1–2.5 s by design.
- Corrections: "negative…" / "I say again…" → pilot reads back **only the corrected elements**; execution errors corrected from radar observation; "confirm cleared level" reveals errors.

### 7.2 Errors (the training value — and a demo star)
- Readback errors: wrong value, transposition, FL/altitude confusion, left/right swap, runway side, omission, wrong callsign, similar-callsign takeover.
- Execution errors: wrong turn direction, wrong level flown, level bust, delayed compliance, ignored instruction.
- Modes: misheard (both wrong), slip (readback wrong only), lapse (flying wrong only).
- Guards: ≤ 1 active per aircraft, ≤ 2 global, never create a loss of separation within 30 s.

### 7.3 Stepped-on transmissions
Two pilots (or pilot + controller) transmit together → both blocked; heterodyne mix (summed filtered voices + ~1.1 kHz tone with 7 Hz modulation, hard clip). A blocked controller instruction isn't applied; pilots re-call after 3–6 s. Graded in the report.

### 7.4 Simulation (`SIM_SPEC.md`)
Bank-limited turns, type-based climb/descent rates, descent/deceleration trade-off, smooth level-offs; autopilot modes (heading, route, localiser, glideslope, hold); STAR/SID following; ILS capture from below only; continuous arrivals + departures with spawn-time separation checks and a difficulty ramp.

### 7.5 Airspace (`data/egkk.json` v2)
Both runways' procedures (61 SIDs/STARs, all resolved), 131 fixes, Gatwick CTR/CTA, London TMA 1–24, Worthing CTA, Director sectors, minimum-altitude areas. All layers toggleable.

### 7.6 Session report
Deterministic rules engine (safety, instruction legality, phraseology, readback monitoring, pilot-compliance monitoring, efficiency) → `report.json` → Claude writes the narrative from it only. UI: score with breakdown, timeline with markers, findings with transcript snippets, catch table, latency stats, coaching points. MUST-tier rules: loss of separation, wake on final, descent below minimum altitude, bad ILS intercept, readback errors caught/missed. Rest SHOULD/COULD.

---

## 8. Team, ownership, timeline

### 8.1 Roles (4 people; with 3, merge C into B + Tudor)
| Person | Owns |
|---|---|
| **Tudor** | Sim core, traffic generator, instruction registry, error policy + clearance/corrections logic, rules engine, data, phraseology sign-off, **pitch** |
| **A — Radar & design** | Radar view, design system, caption strip, session controls, interpolation |
| **B — Voice** | Audio capture/PTT, Scribe client, normaliser, parser, composer + validator, TTS streaming, playback, radio filter, stepped-on mixer, latency metrics |
| **C — Viz & demo** | Viz view (MapLibre/deck.gl), cameras, two-device mode + audio fan-out, demo console/hotkeys, voice design pool, fal features, report UI, backup video |

### 8.2 Timeline (Sat 14:00 → Sun 12:00)
| Window | Tudor | A | B | C | Checkpoint |
|---|---|---|---|---|---|
| **14:00–16:00** | State model, kinematics, typed commands, WS state | Radar canvas, map layers, pan/zoom | `numbers.py`, normaliser, regex parser, typed → `Transmission` | Viz skeleton (map + silhouettes from fake state) | **16:00** typed command turns an aircraft on the radar |
| **16:00–19:00** | STAR following, arrival spawner, ILS + landing, STCA | Data tags, history, vectors, interpolation, caption strip UI | Scribe streaming + commit, ElevenLabs streaming TTS, playback, radio filter | Viz camera presets, plain-English labels; voice design pool generation | **19:00 voice round trip** on the radar |
| **19:00–22:00** | Registry evaluate (unable/query), error policy, corrections + partial readback | Session controls panel, tag states (transmitting pulse, STCA) | Template variants + validator, live caption feeds, latency instrumentation | Two-device mode + audio fan-out (test on venue Wi-Fi), speech bubbles | **22:00 MVP complete**: end-to-end demo with errors + corrections + captions |
| **22:00–02:00** | Departures (3 SIDs) + runway manager; core rules; `report.json` | Polish pass, layer toggles, reduced motion | Stepped-on mixer + frequency manager; word-synced captions | Chase cam on final, director's cut, demo console hotkeys | **02:00 wow tier** mostly in |
| **02:00–06:00** | Sleep shifts: two people sleep 02:00–05:00, two 03:30–06:30. Nobody pushes to `main` unreviewed while others sleep | | | | |
| **06:30–09:00** | Execution errors, remaining rules, seeded demo scenario tuning | Report UI with A's design system | Bug bash on voice; LLM fallback parse | fal Chatterbox MAYDAY, PersonaPlex debrief (COULD), ATIS (COULD) | **09:00 FEATURE FREEZE** |
| **09:00–10:30** | Fill ⚠ VERIFY values, write README, pitch | Visual polish only | Latency tuning, fallbacks tested | Record **backup demo video** on the seeded scenario | |
| **10:30–11:30** | Rehearse the 3-min demo ×3 with the real two-device setup; submit by 11:30 | | | | **SUBMITTED** |

### 8.3 Cut order (if behind at any checkpoint)
1. fal PersonaPlex debrief → 2. scenario-from-sentence, ATIS → 3. 3D models (keep silhouettes) → 4. fal Chatterbox (fall back to ElevenLabs voice) → 5. full rule catalogue (keep MUST rules) → 6. departures → 7. two-device mode (one laptop, viz on the extended display) → 8. word-synced captions (show the whole pilot sentence).

**Never cut:** voice round trip, live controller captions, readback errors + partial corrections, radar, viz view (at least 2D silhouettes), seeded demo, typed fallback.

---

## 9. Demo script (3 minutes, two screens)

Setup: projector shows the **viz view**; Tudor at the controller laptop with headset; C runs the demo console.

| Time | Beat | Screen |
|---|---|---|
| 0:00 | "Every trainee controller needs hundreds of hours in a simulator, and every session needs a room of people pretending to be pilots. I'm a student pilot and I ran an ATC academy — this replaces that room." | Viz overview: live Gatwick traffic in 3D |
| 0:20 | Tudor speaks: "Speedbird one one seven, turn left heading two four zero, descend altitude four thousand." Captions fill in as he speaks; the pilot answers in its own voice; the aircraft banks on screen. | Director's cut follows BAW117 |
| 0:45 | "Every pilot is a separate voice agent with a voice generated for it — listening through ElevenLabs Scribe, reasoning against the simulation, answering in about a second." Latency chip visible. | Split: radar inset |
| 1:00 | **Readback error:** pilot reads back 5,000. Tudor: "Negative, descend altitude four thousand." Pilot: "Four thousand, sorry, Speedbird one one seven." Event feed: ✓ caught. | Caption + event feed |
| 1:25 | **Stepped-on:** two pilots check in at once → squeal. "Two stations blocked, say again." They re-call one at a time. | Two pulsing aircraft |
| 1:45 | **Judge's turn:** hand the headset to a judge for one instruction (big PTT, judge mode). | Judge mode captions |
| 2:10 | **Chase cam:** an aircraft captures the localiser and slides down the glideslope into 26L. (Optional MAYDAY beat with stressed voice if fal is in.) | Chase cam |
| 2:35 | **Report:** score, timeline, the caught error, a loss-of-separation finding. "It knows what was legal — and the trainee can talk it through with an AI instructor." | Report screen |
| 2:50 | "Same engine flipped around trains student pilots on the radio. One platform, both sides of the frequency." | Logo |

Backup: the recorded video on the same seed, ready on both laptops.

---

## 10. Judge Q&A prep
- **"What does ElevenLabs actually do?"** Ears (Scribe Realtime with push-to-talk commits and aviation keyterms), mouths (Flash streaming), and identities (Voice Design per pilot), plus timestamps for word-synced captions and the ATIS.
- **"Why not one big speech-to-speech model?"** It can't touch the simulation reliably — no guaranteed readbacks, no controllable mistakes, and it fights push-to-talk. We use speech-to-speech where it fits: the debrief conversation (fal PersonaPlex).
- **"Can the AI make an aircraft do something impossible?"** No. The AI produces structured instructions; a deterministic flight model with real performance limits flies them; every readback is re-parsed before it's spoken.
- **"How realistic is it?"** Real Gatwick airspace and procedures, type-based performance (OpenAP), bank-limited turns, ILS capture rules — and the rules engine checks separation, minimum altitudes, wake turbulence and intercepts.
- **"Latency?"** Show the chip: ~0.5–0.8 s machine time; the rest is a deliberate, persona-dependent pilot reaction delay.
- **"Who pays?"** ATC training organisations, flight schools, and student pilots preparing for the radio licence (pilot mode).

---

## 11. Submission requirements (checklist)
- [ ] Confirm the exact submission format/time with organisers **today** (Devpost or form? video required? repo link? track + sub-bounty selection fields).
- [ ] Select main track: Voice, Vision & Generative Media; sub-bounties: ElevenLabs, fal.
- [ ] Write-up: problem, solution, how ElevenLabs and fal are used (the tables in §2), architecture diagram, challenges, what's next.
- [ ] 2–3 min demo video (the backup recording doubles as this).
- [ ] Public or shared repo with README: screenshots of both views, setup, `.env.example`, credits (VATSIM UK sector file GPL-3.0, OurAirports, OpenAP, map tiles, 3D model licence).
- [ ] Pitch slides (≤ 4): problem · live demo · architecture + sponsor usage · impact/next.

---

## 12. Accounts, credits, setup
| Item | Action |
|---|---|
| ElevenLabs | Ask reps for credits; else **Creator plan** (4 people testing will exceed Starter). Cancel afterwards |
| fal | Ask reps for credits; get key |
| Anthropic | Key; ask for credits |
| Repo | One private repo, folders per owner, `contracts.py` + `CLAUDE.md` files (per earlier plan) |
| Hardware | Two laptops, **USB headset with mic** for the controller, HDMI/USB-C adapters, phone hotspot as network fallback, room speakers/projector audio |

---

## 13. Risks
| Risk | Mitigation |
|---|---|
| Venue Wi-Fi blocks device-to-device | Hotspot → extended display → tunnel; test Saturday evening |
| Scribe mishears callsigns/numbers | Keyterms, normaliser, fuzzy match on scope only, "say again", typed fallback |
| API outage or credits run out | Fallbacks: fal Wizper / local faster-whisper; fal TTS / browser voices; recorded video |
| Scope overrun | Tiers + cut order + hard 09:00 freeze |
| Exhaustion | Sleep shifts; freeze at 09:00; rehearse, don't build, after 10:30 |
| Demo randomness | Seeded scenario + presenter beat hotkeys |
| Audio feedback in the room | Headset for the controller; room speakers play pilots only |

---

## 14. Verify today
**With the ElevenLabs rep:** one Scribe Realtime session across multiple manual commits? Keyterms updatable mid-session? TTS-with-timestamps on streaming? Voice Design API on our plan and how fast? Credits?
**With the fal rep:** credits; PersonaPlex realtime from the browser; Chatterbox emotion parameter.
**With organisers:** submission format and deadline; whether two devices/projector audio are available at judging.
**Tudor:** the ⚠ VERIFY table in `SIM_SPEC.md` §9 and the phraseology in `readback_variants.json`.

## 15. Technical-depth roadmap (post-MVP)
These are proposed extensions, not claims about the current build. Deliver them after the end-to-end controller MVP is stable; full scope and audience visualization remain separate surfaces.

1. **Observable voice path:** correlate each controller transmission across audio capture, STT, parsing, clearance evaluation, pilot-response planning, composition, TTS, and playback. Show stage timing and failures in a trace view without blocking the voice path.
2. **Reproducible evidence:** maintain labelled parser and speech corpora and report parser accuracy, STT WER, and end-to-end p50/p95/p99 with sample count, model/provider, hardware, and test conditions. Initial targets are parser ≥95% on 100+ labelled utterances, STT WER ≤10% on 50+ clips, and p95 voice-to-caption ≤1,500 ms on 50+ runs; report measurements honestly, whether or not they meet targets.
3. **Probabilistic conflict forecast:** run seeded Monte Carlo continuations of current clearances and show risk probabilities plus optional trajectory fans. This is an advisory estimate, not a clearance, deterministic prediction, or substitute for STCA.
4. **Optional live traffic context:** read a permitted provider snapshot, normalize and label its source/time, then represent the aircraft as simulated AI pilots in this product. Never send instructions to VATSIM or control real aircraft. Confirm provider API terms, attribution, and rate limits before activation.

The shared contracts, simulator parameters, owner assignments, and verification boundary are specified in `CONTRACTS.md` §3.1, `SIM_SPEC.md` §3.6–§3.7, `VOICE_PIPELINE.md`, and the three assignment briefs. Performance budgets remain targets until measured on a defined setup.
