# Assignment — Sanju: Voice Pipeline, Language & Audio

> For Sanju and his AI agent. Read together with `PROJECT_REPORT.md`, `CONTRACTS.md`, `server/contracts.py`, `web/src/protocol.ts`, and **`VOICE_PIPELINE.md` (your main spec)**.
> **You own everything that hears, understands, phrases and speaks** — plus the browser audio engine and the LLM/fal features. This is the part the ElevenLabs judges score.

---

## 1. Mission
Make a controller's push-to-talk transmission become a parsed `Transmission` within a few hundred ms, and make every `PilotResponse` become a varied, validated, natural-sounding radio transmission in that pilot's own generated voice — streamed live, with captions, on every connected screen.

## 2. You own
```
server/voice/    pipeline.py (class VoicePipeline) stt_scribe.py stt_fallback.py normalise.py numbers.py
                 parser/ (intent.py callsign.py segment.py grammar.py llm_fallback.py)
                 tts.py voices.py frequency.py metrics.py
server/pilot/    readback.py (class Composer) validator.py variants_build.py
server/agents/   llm.py narrative.py (class Narrative) scenario.py fal_emergency.py fal_debrief.py
data/readback_variants.json   data/voices.json (generated)
web/src/audio/   index.ts (createAudioEngine) capture.ts player.ts radio.ts mixer.ts worklets/*
server/tests/voice  (+ harness for Tudor's golden set)
```
No React and no layout in `web/src/audio/`. Don't edit other folders.

## 3. Interfaces you PROVIDE (exact — see `contracts.py` / `protocol.ts`)
### 3.1 `class VoicePipeline(VoiceAPI)` — `VoicePipeline(world, pilot, composer, bus, hub)`; keys from env
| Method | Must do |
|---|---|
| `on_ptt("down")` | Emit `ctrl_ptt_down`; tell the frequency manager CTRL is keyed (if a pilot is mid-transmission → mark blocked, emit `overlap` event with `with_ctrl: true`); start/open the Scribe stream |
| `on_audio_in(pcm16)` | Forward 16 kHz PCM frames to Scribe; partial transcripts → `CaptionMsg(stage="partial")` (≤ 5/s) |
| `on_ptt("up")` | Manual commit → final text → `_process_text()`; discard if < 300 ms |
| `on_typed(text)` | `_process_text(text, source="typed")` |
| `_process_text` | normalise → parse (`world.callsigns()`, `world.fixes()`) → `Transmission` → final `CaptionMsg` with `chips`, `plain_english`, `latency` → blocked? (`ctrl_blocked`, stop) → `evals = world.evaluate(tx)` → emit `ctrl_transmission` `{tx, evals}` → `pilot.handle_transmission(tx, evals)` → `speak()` each |
| `speak(resp)` | `silence` → nothing. Else `composer.compose(resp.plan)` → TTS stream (voice = this callsign's voice) → reply scheduler releases at `max(first_chunk, reaction_delay_s)` when the frequency is free → `world.set_transmitting(cs, True)` → `AudioStartMsg`, `AudioChunkMsg`…, `AudioEndMsg` + `CaptionMsg(pilot_text)` then `(pilot_words)` → on end: `set_transmitting(False)`, `pilot.on_readback_spoken(resp, t_end)`, `world.schedule_apply(cs, resp.apply, at=t_end + resp.apply_delay_s)`, emit `pilot_transmission` `{text, kind, plan, timings}` |
| `is_frequency_busy()` | True while CTRL keyed or a pilot transmitting |
| `set_reveal_errors(on)` | Demo toggle: when on, pilot captions carry `error_reveal` (e.g. "said 5,000 — cleared 4,000") for planned errors |

### 3.2 `class Composer(ComposerAPI)` — `Composer(data_dir)`
`compose(plan) -> Readback`: reads back **all and only** `plan.elements`, in varied order/phrasing per persona; numbers → words for `tts_text`; validator re-parses the result and must match the plan's values exactly (including planned errors); on failure try other variants, then a canonical template (`fallback_used=True`). Never alters values. Templates also cover every `ReplyKind` (unable/query via `reason_code`, report/checkin/request via `report_values`, recall, mayday, going_around, looking, established, no_further_clearance, say_again_request, repeat_last).

### 3.3 `class Narrative(NarrativeAPI)` — `await write(report) -> Report`
Claude Sonnet writes `narrative` (≤ 180 words) + 3 `coaching` points **only from the report JSON**, citing finding ids; the prompt forbids new findings or numbers not in the report.

### 3.4 `createAudioEngine(): AudioEngine` (TS, `web/src/audio/index.ts`)
Implements `AudioEngine` in `protocol.ts` exactly:
- **Capture:** `getUserMedia` (echoCancellation off, noiseSuppression on) → AudioWorklet → resample to 16 kHz mono PCM16 LE → ~100 ms frames → `sendBinary`, only between `pttDown`/`pttUp`.
- **Playback:** `handleServerMsg` consumes `audio_start/chunk/end` (and `event` type `overlap`); decode base64 PCM16 (or mp3) → per-transmission ring buffer → radio chain → output. Start on first chunk (the server already applied the reaction delay).
- **Radio chain:** high-pass 300 Hz → low-pass 3,000 Hz → mild tanh waveshaper → output; faint hiss under transmissions; ~150 ms band-passed noise squelch tail at the end; per-stream gain.
- **Heterodyne mixer:** for `blocked: true`, shared `overlap_group`, or an `overlap` event with `with_ctrl`: sum the streams, add a ~1.1 kHz oscillator amplitude-modulated at ~7 Hz (vary ±10% per event), hard clip.
- `onActivity` start/end per tx (tag pulse); `micLevel` (RMS 0–1); `pilotWordClock(tx_id)` = seconds since that tx's playback started; `setPlayback`, `setVolume`.

## 4. Interfaces you CONSUME
| From | What |
|---|---|
| Tudor | `world.callsigns()`, `world.fixes()`, `world.evaluate()`, `world.schedule_apply()`, `world.set_transmitting()`, `world.t`; `pilot.handle_transmission()`, `pilot.on_readback_spoken()`, `pilot.on_blocked()`; the golden set (30 lines at 16:00, full by 19:00); variant review |
| Bhavin | `bus` (`emit`, `now`), `hub.send(msg, roles)`; his app calls your `AudioEngine` and renders your `CaptionMsg`s |

## 5. Requirements

### MUST (by 22:00 MVP)
1. **Stubs by 15:30** (CONTRACTS §6): typed → regex (heading/altitude/speed) → `Transmission`; template composer; `pilot_text` captions; `schedule_apply`; no-op `AudioEngine`.
2. **Scribe v2 Realtime** (`stt_scribe.py`): 16 kHz PCM, manual commit on PTT up, partials → captions; **keyterms** (≤ 50 × ≤ 20 chars): telephony words of callsigns on frequency, fix names, "niner", "QNH", "localiser", "Gatwick", "Director", "Tower", "flight level", "squawk". Persistent session if it supports multiple commits, else open on PTT down and buffer audio until connected.
3. **numbers.py + normalise.py** (VOICE §6): ICAO number words ↔ digits both directions, FL/altitude/QNH/frequency/squawk/runway forms, numeric homophones, joined tokens.
4. **Parser** (VOICE §7): intent detection (incl. `negative`, `I say again`, mid-transmission `correction`), callsign fuzzy match against `world.callsigns()` with thresholds 90/70, clause segmentation, grammar for: heading, turn_by, present_heading, altitude, flight_level, maintain_level, speed (+ constraints), speed_cancel, direct, approach_clearance (type + runway), intercept_localiser, contact (+ unit, frequency), squawk, qnh, report, confirm, expect, go_around; range validation; `chips` + `span` for captions; `parse_path`.
5. **Composer + validator** with a hand-written variant bank first (≥ 6 phrasings per common element type, persona fillers, callsign placement end 80% / start 15% / both 5%, element order 70% as-said / 30% shuffled, no repeat of the last variant per aircraft). **Zero unplanned errors** — the validator is the gate.
6. **ElevenLabs TTS** (`tts.py`): Flash v2.5 streaming via a shared `httpx.AsyncClient` (HTTP/2 keep-alive); PCM output if the plan allows (else mp3); stock-phrase cache per voice ("say again", "standby", "looking", "wilco").
7. **Voices** (`voices.py`): callsign → voice id, deterministic from seed + persona. MUST: a fixed list of 8 diverse library voices. (Generated voices are SHOULD.)
8. **Frequency manager + reply scheduler** (`frequency.py`): CTRL keyed blocks pilot starts; serialise pilot transmissions with 0.3–1.0 s gaps; priority (mayday first); CTRL-over-pilot → both blocked, instruction not delivered (`ctrl_blocked`), pilot `on_blocked` → recall.
9. **Captions**: partial (controller, live), final (chips, plain English, latency), pilot_text at audio start, pilot_words (evenly spread by estimated duration if timestamps unavailable).
10. **Metrics** (`metrics.py`): stamp `t_ptt_down, t_ptt_up, t_final, t_parsed, t_plan, t_tts_req, t_tts_first, t_release, t_audio_end` (wall clock) into `tx.timings` / event data; caption `latency = {"parse_s", "first_audio_s", "perceived_s"}`.
11. **AudioEngine** full (§3.4) incl. radio chain.

### SHOULD (by 02:00)
12. **Generated pilot voices** (ElevenLabs Voice Design): ~12 personas ("calm British captain, 50s", "young Spanish first officer, quick", "German captain, measured", "nervous student pilot", …) generated once and cached to `data/voices.json`; assignment by persona. Fallback to library voices.
13. **Variant bank generation** (`variants_build.py`): at startup if `data/readback_variants.json` missing, Claude generates ~15 phrasings per (persona × element type) with slots; validate each (fill with 200 random values → validator); cache; Tudor reviews.
14. **Heterodyne / stepped-on** end-to-end: `force_overlap` inject → two `speak()` calls released together with a shared `overlap_group`.
15. **Word-synced pilot captions** via ElevenLabs TTS-with-timestamps (char alignment → words) → `CaptionMsg(pilot_words)`.
16. **LLM parser fallback** (`llm_fallback.py`, Claude Haiku): strict tool schema derived from `Instruction`; inputs = raw + normalised text, callsigns, fixes, runways; "fill `ambiguities`, never guess"; prompt caching; merge only failed clauses.
17. **Narrative writer** (§3.3).
18. **Golden-set harness**: `pytest` reporting exact-match % and per-type confusion; target ≥ 90%.

### COULD (Sunday 05:00–09:00, only if MUST/SHOULD are solid)
19. **fal Chatterbox emergency voice** (`fal_emergency.py`): for `mayday`/`pan`/persona `stressed`, synthesise via fal Chatterbox with high emotion/exaggeration; `AudioStartMsg.emotion="stressed"`. Falls back to ElevenLabs.
20. **fal PersonaPlex debrief** (`fal_debrief.py` + `web/src/audio/debrief.ts`): server mints/proxies the fal realtime session; persona prompt = instructor + compact report summary; browser module exposes `startDebrief(summary)` / `stopDebrief()`; Bhavin adds the button. (Needs a small additive contract note for the endpoint — coordinate with Bhavin.)
21. **fal Wizper fallback STT** (`stt_fallback.py`) on PTT up when Scribe errors; then local faster-whisper.
22. **Generated ATIS** (fixed voice, looped on demand) and **scenario-from-sentence** (`scenario.py`, Sonnet → `SessionSettings` + injects schedule) — each needs an additive control action; agree with Bhavin first.
23. **Speculative execution** (VOICE §3 item 7).

## 6. Implementation notes
- Readback formatting rules (must match Tudor's phraseology review): headings digit-by-digit with "heading" optional per variant; "flight level eight zero"; "altitude four thousand" / "four thousand" per persona; 9 → "niner"; QNH/squawk/frequency digit-by-digit ("one two four decimal two two five"); runway "two six left"; callsign at the end unless the variant says otherwise.
- Validator grammar is **pilot phrasing** (no "turn", allows "-ing" forms, "left two four zero"): reuse `numbers.py` and the clause grammar with a pilot-side pattern set.
- `plain_english` for captions: "BA117 → turn left 240°, descend 4,000 ft" (use `spoken_plain` from `public_state()` aircraft if available).
- Keep the PTT path free of LLM calls unless the regex parse fails.
- Never let a failed external call kill the session: every API call has a timeout (STT 3 s, TTS 4 s, LLM 4 s) and a fallback (typed caption / browser voice is acceptable).
- Cost control: no TTS for blocked transmissions beyond what plays; cache stock phrases; generated voices once.

## 7. Acceptance tests
- `numbers`: round-trip property tests for headings, levels, altitudes, speeds, QNH, squawks, frequencies, runways.
- Parser: golden set ≥ 90% exact match; intents (negative / I say again / correction / confirm / say again / read back) classified correctly on 20 crafted cases.
- Composer/validator: every template × 200 random values → `parse(fill) == values`; planned-error plans render the error; 1,000 random plans → 0 unplanned discrepancies.
- Frequency: CTRL keyed during pilot audio → `ctrl_blocked` + `overlap`; pilot re-call arrives 3–6 s later; two forced-overlap responses share an `overlap_group`.
- Latency bench (`tests/voice/bench_latency.py`): 20 recorded WAVs through the real pipeline → print per-stage p50/p90; regex path p90 ≤ 1.2 s machine time.
- AudioEngine: manual test page `web/src/audio/dev.html` — PTT capture level meter, play a fixture transmission with radio chain, play two overlapping → squeal.

## 8. Timeline & checkpoints
| Time | Deliver |
|---|---|
| 14:30–15:30 | Stubs (pipeline typed path, composer, no-op AudioEngine) on `main` |
| 15:30–16:00 | **16:00 integration 1** (typed → readback caption → aircraft turns) |
| 16:00–19:00 | Scribe streaming + commit + partial captions; ElevenLabs streaming; AudioEngine capture + playback + radio chain; numbers/normaliser → **19:00 voice round trip** |
| 19:00–22:00 | Full grammar + callsign matcher + intents; hand-written variants + validator; frequency manager; captions + metrics → **22:00 MVP** |
| 22:00–01:00 | Heterodyne end-to-end; voice design pool; LLM fallback; variant generation; word-synced captions; golden harness |
| 01:00–04:30 | Sleep |
| 04:30–09:00 | Narrative; fal Chatterbox; fallbacks; latency tuning; (PersonaPlex) → **09:00 freeze** |
| 09:00–10:30 | Benchmark numbers for slides; README voice section; sponsor write-up paragraphs (ElevenLabs, fal) |
| 10:30–11:30 | Rehearse, submit |

## 9. Overflow rule
If behind at 22:00, cut in order: word-synced captions (show the whole sentence) → variant generation (keep the hand-written bank) → generated voices (keep library voices) → fal features. Never cut: Scribe live captions, validator, partial-correction readbacks, radio chain, frequency blocking.

## 10. Definition of done
Voice round trip works on the venue network with p50 machine latency shown on screen; zero unplanned readback errors over a 20-minute session; captions live on both devices; AudioEngine satisfies `protocol.ts`; contract tests green.

## 11. Post-MVP technical-depth track (planned)
Start only after the complete voice-to-simulation MVP works. Own per-transmission instrumentation and reproducible voice benchmarks; coordinate any contract additions with Bhavin before implementation.

- Emit `TraceMsg`s with the exact 15 stage names, categories and detail keys in `VOICE_PIPELINE.md` §20.1 (`speech` … `apply_scheduled`), anchored at PTT up (or compose start), stamped with `perf_counter_ns`: provisional after `validate`, updated at release, final at audio end; one `blocked` trace for a blocked transmission. `machine_ms` must equal the latency chip; never reveal a planned error in `pilot_decision` unless "Reveal errors" is on.
- Support provisional transcript revisions and the final transcript as distinct events linked to the same trace. Keep audio and transcripts out of trace logs by default; record only text when explicitly allowed by the existing run-data policy.
- Store run traces append-only under the run directory and make UI payloads optional/additive. Do not let tracing failures interrupt the voice path.
- Build `server/tests/voice/bench/` scripts and fixed input manifests for parser accuracy, STT word error rate, and end-to-end latency. Report corpus/sample count, provider/model/version, hardware, conditions, and p50/p95/p99; keep baselines versioned and comparable.
- Acceptance targets for the first benchmark pass: parser ≥95% on 100+ labelled utterances; STT WER ≤10% on 50+ clips; p95 voice-to-caption ≤1,500 ms on 50+ runs. Publish actual measurements and conditions; a target is not a claim of achieved performance.
- Add deterministic parser fuzz/variant coverage and validate generated pilot phrase varieties against canonical fallback phrases. Avoid unstable network-provider tests in the default unit test suite.

## 12. Change requests (Tudor, 15 Nov) — speech
Spec: `VOICE_PIPELINE.md` §6 items 5–5a and §11.1.
1. **Lima is "LEE-mah"**: respell phonetic letters for TTS where ElevenLabs gets them wrong (Lima "Leema", Quebec "keh-BECK", Hotel "hoh-TELL", Papa "pah-PAH", Sierra "see-AIR-rah" …); display text unchanged; the validator maps respellings back.
2. **Spelled RNAV points:** parse "kilo kilo sierra two five" → KKS25 and speak such points that way in readbacks.
3. **`QUERY_FIX_PASSED` reply** once Bhavin adds the reason code: "Confirm direct TUFOZ, we've passed it, Speedbird 117".
4. **Pronunciation check script/page** so Tudor can approve letters, navaids and fixes by ear.
