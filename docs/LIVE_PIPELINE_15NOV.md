# Live pipeline screen: `/pipeline` (15 Nov 2026, Sanju)

**What it is.** A read-only page that shows the signal path live. Open it on a laptop next to the demo
machine. It lights up:
- each time someone talks into the mic;
- each time an API is called;
- each time a pilot declares a MAYDAY.

**Where it lives.** Branch `sanju/live-pipeline`, stacked on `sanju/challenge` (PR #3). Merge #1, #2 and #3
first.

## How to use it at the demo

1. Start the server on the demo machine as usual (`python -m uvicorn server.main:app --host 0.0.0.0 --port 8000`).
2. Put the laptop on the **same Wi-Fi**.
3. Open `http://<demo-machine-ip>:8000/pipeline` on the laptop. To get the full address:
   - on the radar: **Pipeline ↗** in the top bar;
   - or: side panel → **Connection / second device**, which shows the LAN link with the session code;
   - or: at the bottom of the pipeline page itself.
   Any number of screens can watch at once. They're viewers (role `viz`), so they can't transmit.
4. Talk on the radar as normal. Nothing else to switch on.

Checked live on 15 Nov: page loaded from the LAN address (`192.0.0.2`) while a scripted controller
typed one call, spoke one call through the mic path, and injected a mayday.

## What the page shows

- **Diagram:** four lanes (browser, Python server, AI APIs, flight simulator) and ten stages.
  - A stage glows **while it runs**, with a live timer.
  - It then turns **green with its measured time**, or **red if it failed**.
  - A yellow packet runs along the wire into each stage as it starts.
  - API stages carry an "API" tag and show which service ran (Scribe or Wizper; Flash or Chatterbox;
    Claude for parser fallback, coach tip or debrief).
- **Header:**
  - "TRANSMITTING" with a timer while the mic is keyed;
  - total API calls;
  - the last response time (controller release → pilot on air, replies only).
- **MAYDAY / PAN PAN:**
  - a red banner with the callsign;
  - the voice and radio stages turn red, and packets on that path are red.
- **What was said:** the controller's final transcript and the pilot's readback text.
- **Side panel:**
  - per-service API stats (calls, average time, failures);
  - a scrolling activity log with server timestamps (an ended call replaces its "calling…" row).

## How it works

`server/voice/activity.py` sends an `ActivityMsg` (type `"activity"`) to **every** connected screen.
- One message is sent per:
  - mic key or release;
  - API call start and end;
  - finished local stage;
  - mayday;
  - on-air change.
- Start and end of one call share an `id`. Times are server-measured (`perf_counter`).
- Messages never carry audio, transcript text or keys: only service names, timings and the callsign.
- Like the traces, it fails open, so it can never break the radio.

| Stage (`node`) | Sent from | Service shown |
|---|---|---|
| `mic` | `pipeline.on_ptt` down / up | Browser microphone (time = speech length) |
| `stt` | PTT down → transcript final | ElevenLabs Scribe v2 Realtime (time = release → final transcript) |
| `stt` | backup path, warm-up | fal Wizper |
| `parse`, `sim`, `pilot`, `readback` | `_process_text`, `_speak` | local, with measured ms |
| `claude` | parser fallback, coach tip, debrief (`agents/narrative.py`), warm-up | Claude Haiku 5.5 |
| `voice` | `_produce` per engine attempt, ATIS, warm-up | ElevenLabs Flash v2.5 or fal Chatterbox |
| `pilot` + status `emergency` | `_speak` when `resp.kind` is mayday / pan | MAYDAY or PAN PAN |
| `radio` | on air → off air | Radio (Web Audio); error if stepped on |
| `apply` | page listens to the existing `instruction_applied` event | the aircraft responding |

The page also uses the existing `caption`, `trace` and `event` messages, which every screen already
receives.

## Files

| File | Owner | Change |
|---|---|---|
| `server/voice/activity.py` | new | emitter (`emit`, `Span`, `call`) |
| `server/voice/pipeline.py`, `tts.py`, `stt_fallback.py`, `parser/llm_fallback.py` | Sanju | activity at each call site |
| `server/agents/narrative.py` | Sanju | debrief Claude call reported |
| `server/contracts.py`, `web/src/protocol.ts` | Bhavin | additive: `ActivityMsg`, `ActivityNode` |
| `server/main.py` | Bhavin | serves `/pipeline` (same `index.html`) |
| `web/src/app/main.tsx` | Bhavin | `/pipeline` renders `PipelineLive` instead of `App` |
| `web/src/app/App.tsx` | Bhavin | "Pipeline ↗" link; LAN link in Connection / second device |
| `web/src/pipeline/PipelineLive.tsx`, `pipelineModel.ts`, `pipeline.css`, `tests/pipeline.test.mjs` | new | the page and its pure model |

As with PRs #1 to #3, the files marked Bhavin are blocked by the ownership hook, so the commit was made
with `--no-verify` on Sanju's decision. The branch is pushed, not merged.

## Tests

- `server/tests/voice/test_activity.py` (8):
  - emitter: start/end with duration, errors re-raised, ends once, no hub → no-op, no text in messages;
  - pipeline: a typed call lights parse → sim → pilot → readback → voice → radio in order;
  - mic and Scribe spans, with release → final time;
  - a short blip still closes the Scribe span;
  - a mayday is flagged `emergency` and the voice call is labelled `mayday`.
- `web/src/pipeline/tests/pipeline.test.mjs` (6): running → done → idle; packets follow the flow; the mayday
  path; errors and warm-ups; captions, apply and response time; geometry and time formatting.
- Full suite: 1,141 Python tests, 39 web tests, smoke test and build pass.
- Live, with the real server, ElevenLabs, Claude and fal:

  | Call | What the page showed |
  |---|---|
  | Typed call | full path; readback in 157 ms; coach tip in 1.0 s; response 1.79 s |
  | Spoken call | mic 6.5 s; Scribe 0.28 s after release; response 1.14 s |
  | Mayday, first run | Chatterbox 9.7 s, red banner |
  | Mayday, second run | Chatterbox hit its 10 s limit and failed (shown red); ElevenLabs took over in 0.19 s |

**Seen in the live run (not changed here).** fal Chatterbox is slow: 9.7 s in one run, over its 10 s limit
in the other. The fallback works and the page makes it visible. If the demo uses a mayday, consider
triggering one early so fal is warm.

## Merge of `main` (15 Nov, after Tudor's rename)

`main` (4 new commits, including the SweatboxAI → VoiceVector rename) was merged into this branch:
- `server/sim/evaluate.py`: only a stale comment conflicted. `main`'s version was taken for that hunk. Our
  approach-speed rule (160–200 kt below 5,000 ft) stays.
- `server/sim/world.py`: Tudor's `main` publishes the same flight-plan fields as PR #1. His version of
  those lines and his `_track_distance` were kept (it also handles the hold), and ours was removed.
- `web/src/app/App.tsx`: VoiceVector logo from `main`; our pop-out and **Pipeline ↗** links kept.
- New text from our branches renamed too: the pipeline page header, tab title and lane label.

After the merge: 1,143 Python tests and 104 web tests pass (`node --test` over `web/tests` and
`web/src/**/tests`), plus the smoke test and the build.
