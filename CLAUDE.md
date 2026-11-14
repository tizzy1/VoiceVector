# SweatboxAI — shared agent context

ATC training simulator for the Imperial AI Hackathon (Track 2: Voice, Vision & Generative Media;
sub-bounties ElevenLabs + fal). A trainee controller speaks on push-to-talk; AI pilots read back in
generated voices (sometimes with planned mistakes) and fly the instructions on a Gatwick radar; a
second screen shows a cinematic 3D view. Deadline: Sun 15 Nov 11:30.

## Read first
1. `docs/PROJECT_REPORT.md` — what we're building and why, tiers, timeline, demo
2. `docs/CONTRACTS.md` + `server/contracts.py` + `web/src/protocol.ts` — module boundaries (source of truth)
3. Your assignment: `docs/ASSIGNMENT_TUDOR.md` | `ASSIGNMENT_SANJU.md` | `ASSIGNMENT_BHAVIN.md`
4. Detail specs: `docs/VOICE_PIPELINE.md`, `docs/SIM_SPEC.md`

## Hard rules
- Work only in folders you own (CONTRACTS §1). Never edit `contracts.py`/`protocol.ts` without the §7 process.
- The LLM never moves aircraft, decides legality, chooses errors, or writes numbers into readbacks.
- Deterministic, seeded randomness everywhere (demo must replay identically).
- Units: NM, ft, kt, ft/min, degrees true; positions are local NM from the RWY 26L threshold.
- Keep `main` runnable. Run `pytest` (server) / `npm run build` (web) before pushing.
- Prefer the MUST tier over polish until the 22:00 MVP checkpoint.

## Stack
FastAPI + uvicorn + pydantic v2 (Python 3.12) · Vite + React + TS + zustand · Canvas 2D (radar) ·
MapLibre GL + deck.gl (viz) · Web Audio / AudioWorklet · anthropic, elevenlabs, fal-client, httpx, rapidfuzz.
