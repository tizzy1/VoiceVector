# Judge challenge and "hear what you missed" (15 Nov 2026, Sanju)

**Where it lives.** Branch `sanju/challenge`. It is built on top of `sanju/team-sim` (PR #1) and
`sanju/coach` (PR #2), because it uses the judge scenario and the coach scores. Merge #1 and #2 first;
this PR then shows only its own commits.

## 1. Judge challenge

**What judges see.**
- **Controller screen:** a yellow card at the top left of the radar: "JUDGE CHALLENGE · LAND BAW117",
  with a clock, number of calls, phraseology average, go-arounds and a live score. After touchdown it
  shows the rank and a name box for the leaderboard.
- **Second screen (projector):** a leaderboard (top 8) in the corner whenever the judge scenario
  is running, with the current judge's row highlighted.

**Rules.** Active only in the `judge` scenario (one A320 holding at TIMBA, 5,000 ft; PR #1).
- **Clock:** starts at the judge's first transmission and stops at touchdown, in simulator seconds.
  At 2× sim speed, real time is half.
- **Score (0–100):**
  - time: 40 points, full within 6 minutes, falling linearly to 10 at 20 minutes;
  - phraseology: 40 × the coach's average score (75 % assumed until the first coached call);
  - safety: 20 points, minus 10 per go-around and minus 5 per STCA alert;
  - plus 5 for each pilot readback error caught, minus 5 for each missed.
- **Leaderboard:** top 10, saved in `runs/leaderboard.json`, so it survives restarts (delete the file
  to reset). New entries are "Judge N" until a name is saved with control `challenge_name`.

**Files.**

| File | Owner | Change |
|---|---|---|
| `server/challenge.py` | new | `Challenge`: bus listener, scoring, leaderboard |
| `server/session.py` | Bhavin | creates the challenge, subscribes it, sends `ChallengeMsg` each tick while active, handles `challenge_name` |
| `server/voice/pipeline.py` | Sanju | `on_coach` hook: coach scores feed the challenge |
| `server/contracts.py`, `web/src/protocol.ts` | Bhavin | additive: `ChallengeEntry`, `ChallengeMsg`, control action `challenge_name` |
| `web/src/ui/Challenge.tsx`, `challengeFormat.ts`, `challenge.css` | new | card + leaderboard |
| `web/src/app/App.tsx` | Bhavin | state + handler; card on the radar; leaderboard on `/viz`. The scenario picker now follows the session's real scenario. |

## 2. "Hear what you missed"

When a pilot reads back a planted mistake (error modes `consistent` / `readback_only`), the
pipeline keeps that transmission's audio (in memory, at most 30 clips per session). In the debrief,
every readback finding that points at such an error gets a player and the facts: "Hear what the
pilot said · read back 250, you cleared 240". The clip plays through the same radio sound as the game.

| File | Owner | Change |
|---|---|---|
| `server/voice/replay.py` | new | `ReplayStore` (keep / WAV with the radio filter) |
| `server/voice/pipeline.py` | Sanju | keeps the PCM of readbacks that carry spoken errors |
| `server/main.py` | Bhavin | `GET /api/replay/{error_id}` → `audio/wav`, 404 if not recorded |
| `web/src/report/ReportView.tsx`, `report.css` | Bhavin | `MissedReplay` player on readback findings |

Clips live only as long as the server session. After a restart, older reports show no player.

## Ownership

The files marked Bhavin are blocked by the ownership hook. As with PRs #1 and #2, the commits were
made with `--no-verify` on Sanju's decision, and the branch is pushed, not merged.

## Tests

- `server/tests/voice/test_challenge.py` (7): judge-only, clock, score parts, leaderboard top 10 +
  persistence + names, a real simulator run that lands and scores, and the session control path.
- `server/tests/voice/test_replay.py` (3): only spoken errors kept, radio WAV, oldest dropped, the
  pipeline captures the audio, the route serves WAV / 404.
- Live: a scripted judge run against the real server and voice pipeline (see the PR for the result).
