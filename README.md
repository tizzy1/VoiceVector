# VoiceVector

**Talk to AI pilots. Control real Gatwick airspace.**

VoiceVector is an air traffic control training simulator. A trainee controller speaks on
push-to-talk; AI pilots answer in their own voices, sometimes make realistic mistakes on purpose,
and fly the instructions on a live radar of London Gatwick. A second screen shows the same session
in 3D so anyone watching can follow it.

*Imperial AI Hackathon 2026 · Track 2: Voice, Vision & Generative Media*

---

## Why
Every trainee controller needs hundreds of hours in a simulator ("sweatbox"), and every session
needs a room of people pretending to be pilots. They are the bottleneck, and they can't reliably
make the subtle mistakes a trainee most needs to learn to catch. VoiceVector replaces that room.

## What it does
- **Speak, and a pilot answers.** Live transcription while you talk, a varied readback in a
  distinct voice through radio static, and the aircraft visibly turns.
- **Pilots that get it wrong on purpose:** a wrong level in the readback, a turn the wrong way, a
  look-alike callsign taking someone else's clearance. Catch it with "negative…" and the pilot
  re-reads only what changed.
- **Stepped-on transmissions:** two stations at once produce the heterodyne squeal, and the
  blocked instruction is never delivered.
- **A debrief that knows the rules:** a scored report of what was legal, what was missed and
  what to practise.
- **A 3D audience view** of the same session, with chase cameras and plain-English captions.

---

## Under the hood

```
 Headset ─► PTT audio ─► Speech-to-text ─► Parser ─► Pilot logic ─► Readback ─► Validator ─► Pilot voice
 (browser)               ElevenLabs        grammar   errors and     varied      re-parses    ElevenLabs
                         Scribe RT         (+ ML,    corrections    phrasing    every word   Flash
                                           + Haiku)       │
                                                          ▼
                          Deterministic Gatwick simulator (1 Hz) ─► radar + 3D view + rules engine
```

### Voice pipeline
- **Real-time speech-to-text:** ElevenLabs Scribe v2 Realtime streams while you speak and commits
  when you release the key. fal Wizper is kept warm as a backup.
- **Parser:** a hand-built ATC grammar scores **100/100** on our golden set of real controller
  phrasing, including sloppy speech and speech-to-text slips. Claude Haiku 5.5 is called only for
  lines the grammar can't parse.
- **Optional trained parser:** a CRF tagger trained on **1,879 real controller transmissions**
  (UWB-ATCC) plus 3,000 synthetic Gatwick lines. It reaches 0.98 span F1 on held-out recordings
  (agreement with the grammar's labels) in **0.16 ms** per transmission. Off by default; see
  [docs/ML_PARSER.md](docs/ML_PARSER.md).
- **Readbacks you can trust:** pilots phrase things differently every time, but every readback is
  re-parsed before it is spoken, so a pilot never makes a mistake that wasn't planned.
- **Voices:** 14 pilot voices designed with ElevenLabs Voice Design, spoken by Flash v2.5 through
  a radio chain fitted to real VHF recordings. MAYDAY calls use a stressed voice via fal
  Chatterbox.
- **Pronunciation done right:** ICAO phonetics ("Lima" is *LEE-mah*), "niner", navaids by station
  name ("direct Seaford", "Midhurst one X-ray").
- **Live pipeline trace (`T`):** 15 measured stages for every transmission, from speech to
  playback, shown as a waterfall.

### Simulator
- **Real Gatwick airspace** from the VATSIM UK sector file: 131 fixes, 61 SIDs and STARs, and 29
  airspace volumes (CTR, CTA, London TMA, Worthing CTA).
- **Realistic flight:** OpenAP performance per type, bank-limited turns, descent/speed
  trade-offs, wind, holds at WILLO and TIMBA, ILS localiser and glideslope capture rules,
  departures on SIDs with a runway manager.
- **Flight plans:** origin, destination and a requested flight level chosen by the semicircular
  rule; squawks; EuroScope-style editable lists.
- **Deterministic and seeded:** the demo replays identically every time. AI never moves an
  aircraft or decides what is legal.

### Safety and assessment
- **STCA:** loss-of-separation, 2-minute prediction and wake-turbulence alerts.
- **Probabilistic conflict forecast:** 64 seeded possible futures per aircraft, every 2 seconds,
  giving the chance two aircraft lose separation (about 6 ms per pass).
- **Rules engine:** 29 deterministic rules across safety, instruction legality, phraseology,
  readback monitoring and pilot-compliance monitoring, with 13 kinds of planned pilot error.
  Claude only writes the debrief narrative, from the report.

### Platform
- FastAPI and WebSockets on one server; React and Canvas 2D for the radar; MapLibre and deck.gl
  for the 3D view; Web Audio worklets for capture, the radio effect and the heterodyne mixer.
- Read-only VATSIM traffic snapshots become simulated AI pilots (never controlled live).
- **1,150 server tests and 65 web tests**, plus an end-to-end smoke test over real WebSockets.

---

## Run it

You need **Python 3.12** and **Node.js 20.19+** (or 22.12+).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then add your API keys (below)
(cd web && npm ci && npm run build)
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Then open **http://localhost:8000**, click *Enable microphone and pilot audio*, hold **Space** and
talk. Try: *"Speedbird one one seven, turn left heading two four zero, descend altitude four
thousand feet."*

For the 3D view, open the address in the controller's second-device panel on another device, or
`/viz` in a second window.

### API keys (`.env`)
| Key | Used for |
|---|---|
| `ELEVENLABS_API_KEY` | Speech-to-text and pilot voices |
| `ANTHROPIC_API_KEY` | Parser fallback and the debrief narrative |
| `FAL_KEY` | Backup speech-to-text and the stressed MAYDAY voice |

### Useful keys in the app
`Space` talk · `T` pipeline trace · `D` demo shortcuts · `J` judge mode · `1` reset demo ·
`8` end session and report. Everything else: [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

### Tests
```bash
python -m pytest server/tests
python server/tests/integration/smoke_test.py --real
(cd web && node --test tests/*.test.mjs && npm run build)
```

For front-end work, run `npm run dev` in `web/` next to the Python server (Vite proxies `/api`
and `/ws` to port 8000).

---

## Documentation
| Doc | What's in it |
|---|---|
| [USER_GUIDE.md](docs/USER_GUIDE.md) | Every screen, control and shortcut |
| [PROJECT_REPORT.md](docs/PROJECT_REPORT.md) | The product, the pitch and the plan |
| [VOICE_PIPELINE.md](docs/VOICE_PIPELINE.md) | Speech, parsing, pilot logic, errors, corrections, latency |
| [SIM_SPEC.md](docs/SIM_SPEC.md) | Flight model, instructions, rules engine, airspace, scope |
| [CONTRACTS.md](docs/CONTRACTS.md) | Module boundaries and the WebSocket protocol |
| [ML_PARSER.md](docs/ML_PARSER.md) | Building and enabling the trained parser |
| [LIVE_IMPORT.md](docs/LIVE_IMPORT.md) | VATSIM traffic snapshots |

## Team
- **Tudor** — simulator, pilot logic, rules engine, aviation data and phraseology. Student pilot;
  founded the ATC24 Academy.
- **Sanju** — voice pipeline: speech-to-text, parser, readbacks, voices, audio, LLM and fal
  features.
- **Bhavin** — platform: server, controller scope, 3D audience view, design, demo tools and report.

Contributors: each clone installs the ownership hook once (`sh tools/git/install-hook.sh <name>`)
and follows the git rules in `docs/`.

## Credits
- Gatwick geometry and procedures: [VATSIM UK Sector File](https://github.com/VATSIM-UK/UK-Sector-File)
  (GPL-3.0) and OurAirports. Simulation use only, not for navigation.
- Aircraft performance: [OpenAP](https://github.com/TUDelft-CNS-ATM/openap) (TU Delft).
- Training transcripts: [UWB-ATCC](https://huggingface.co/datasets/Jzuluaga/uwb_atcc),
  Šmídl et al., University of West Bohemia (CC BY-NC-SA 4.0).
- Fonts: Inter and JetBrains Mono (SIL Open Font License).
- Voice and AI: ElevenLabs, fal, Anthropic Claude.
