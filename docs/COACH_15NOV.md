# Phraseology coach (15 Nov 2026, Sanju)

**What it does.** After each controller transmission, a small card over the radar scores the call
(0–100) and lists what was good or should change, in UK (CAP 413) style. A moment later Claude Haiku
adds one coaching sentence that quotes the better wording. It is built for the judge scenario: judges
are the controllers and get feedback on their own voice immediately.

**Where it lives.** Branch `sanju/coach`, based on `main` and independent of `sanju/team-sim` (PR #1).
Both edit `App.tsx`; the coach card was placed after the waiting block so the two merge without
conflicts in either order (checked with a real test merge).

**On screen** the card never names the model: it shows "Reviewing…" while the tip is on its way,
then the tip labelled "Tip".

## How it works

1. **Rules (instant, deterministic):** `server/voice/coach.py` → `review()`. Checks:
   - callsign present, and first;
   - flight level above / altitude at or below the 6,000 ft transition altitude;
   - QNH with the first clearance down to an altitude;
   - "heading", "feet"/"altitude" and "knots" said with the numbers;
   - runway named on approach clearances, frequency on transfers;
   - at most three instructions per call, "niner", speaking rate (over 4.6 words/s), parts not understood.

   Scoring: −15 per "fix", −6 per "tip". Acknowledgements ("readback correct") are not coached.
2. **Claude tip (off the critical path):** `claude_tip()` sends the transcript and the rule notes to
   Claude Haiku 5.5 with a 4 s deadline and asks for one sentence, as JSON. Guard rails:
   - numbers may only come from the call or the rule notes;
   - at most 260 characters;
   - any failure means no tip, and the card shows the rules only.

   Measured: 0.9–3.0 s.
3. **Pipeline:** `VoicePipeline._coach` runs after the pilot's reply is scheduled, so it never delays the
   readback. It sends a `CoachMsg`, then the same `tx_id` again with `tip` when Claude answers. Any
   exception is logged and ignored. Switch off with `pipeline.coach_enabled = False`.
4. **Card:** `web/src/ui/CoachCard.tsx` (+ `coach.css`, `coach.ts`), bottom-right of the radar on the
   controller screen, with the score colour-coded (green ≥ 90, amber ≥ 70, red below 70). It can be
   hidden and reopened.

Only what the controller said is judged, never the pilot's planned errors, so nothing is given away.

## Files outside Sanju's area (Bhavin's), all additive

| File | Change |
|---|---|
| `server/contracts.py` | New `CoachNote`, `CoachMsg` (type `"coach"`) |
| `web/src/protocol.ts` | Same shapes, `CoachMsg` added to `ServerMsg` |
| `web/src/app/App.tsx` | 5 lines: import, `coach` state, `"coach"` message handler, `<CoachCard>` over the radar |
| `web/src/ui/CoachCard.tsx`, `coach.ts`, `coach.css`, `tests/coach.test.mjs` | New files |

The ownership hook blocks these, so the commit was made with `--no-verify` on Sanju's decision. The
branch is pushed, not merged.

## Tests

- `server/tests/voice/test_coach.py` (15): every rule, scoring, acknowledgements, the Claude number
  guard, no tip without a key, and the pipeline sending rules then tip without breaking the reply.
- `web/src/ui/tests/coach.test.mjs`: tip merge.
- Live check: "Speedbird 117 turn left 240 descend altitude 4000 feet" → 79, notes "include
  'heading'" and "include the QNH", then Claude's tip with the corrected wording; pilot reply not delayed.
