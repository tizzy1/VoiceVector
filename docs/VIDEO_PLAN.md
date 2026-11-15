# Submission video plan (2 minutes)

The judges mark what they see in the video and what we say afterwards. There are no slides and no
live demo in the judging round: the video plays, then 6 minutes of questions.

**Approach:** one continuous, real run of the **Judge** scenario recorded from the controller screen,
with the `/pipeline` page in a corner, cut down with labelled time-lapses and a voiceover added
afterwards. The judge scenario is the whole task from start to finish: one aircraft (BAW117,
holding at TIMBA at 5,000 ft), check-in, vectors, ILS, landing, score. `/pipeline` shows where each
bounty tool is used without us having to say it.

## Recording setup

- **Main canvas:** controller radar (`/`), full screen, scenario **Judge**, 2× sim speed.
- **Picture-in-picture, bottom right (about 25%):** `/pipeline`. Every Scribe, Claude, ElevenLabs
  and fal call lights up as it happens.
- **Cutaways:** `/viz` on the chase camera for the landing (the advisory conflict-forecast panel can
  stay hidden; there is only one aircraft), and the report screen at the end.
- **Tool:** OBS, browser windows as sources. Capture system audio so the pilots' radio voices are
  recorded.
- **Voiceover:** record the run first, then narrate over it and lower the radio audio except during
  the key exchanges.
- **Before recording:**
  - delete `runs/leaderboard.json`;
  - leave `ML_PARSER` unset (see `ML_PARSER.md`);
  - trigger one mayday so fal Chatterbox is warm (from cold it took about 10 s).

## Shot list

| Time | On screen | Narration |
|---|---|---|
| **0:00–0:15** Problem | Radar, BAW117 holding at TIMBA | "Trainee air traffic controllers need hundreds of hours of simulator practice, and every hour needs a person pretending to be the pilots. That's the bottleneck at training units like the ATC academy Tudor ran. VoiceVector replaces those pseudo-pilots with AI pilots." |
| **0:15–0:30** | Push-to-talk: "Speedbird 117, turn left heading 360, descend altitude 3,000 feet, QNH 1013". Caption fills in live, the pilot answers in its own voice, the aircraft turns, `/pipeline` lights up. | "The controller talks on push-to-talk. The pilot answers in about a second and flies it." |
| **0:30–0:50** | Press **4** before the next call. The pilot reads back a wrong value; the controller says "Negative, …" and the pilot repeats only the corrected part. | "The pilots make realistic readback mistakes on purpose. The trainee has to catch them, like the real job." |
| **0:50–1:05** | Coach card score and tip; "cleared ILS approach runway 26 left" | Point at the coach feedback. |
| **1:05–1:20** | Time-lapse on `/viz` chase cam: localiser and glideslope capture, landing. On-screen label "4× speed". | "The aircraft flies real Gatwick procedures with real performance limits." |
| **1:20–1:30** | Challenge result and report: score breakdown, caught error | "Automated: everything after the controller's voice. Manual: the controller. Simulated: the traffic and weather. The error was triggered on purpose for this video." |
| **1:30–1:50** How it's built | `/pipeline` full screen during one call, then a 3-second mayday clip with fal Chatterbox lit red | "ElevenLabs Scribe for listening, Flash and Voice Design for 14 pilot voices, fal Chatterbox for stressed emergency calls, Claude only for understanding odd phrasing and coaching. A deterministic simulator flies the aircraft, never the AI." |
| **1:50–2:00** What's new | Leaderboard | "Commercial ATC simulators use scripted pseudo-pilots or basic speech recognition. Ours makes gradeable human mistakes, sounds like a real radio and runs in a browser. In our tests: [X of Y] runs landed, and 19 of 20 spoken clearances were understood exactly." |

## Things to get right

- **Say what's manual or mocked** in the run itself (the 1:20 line), not just in Q&A.
- **Label every time-lapse.** A clean landing takes about 11 sim minutes, about 5.5 real minutes at 2×.
- **Reliability number:** quote it from repeated runs. We have 19/20 for speech understanding
  (`runs/bench_latency.json`), but only two full judge-scenario landings so far, which is too few to
  quote. Fill in `[X of Y]` after a batch of repeated runs.
- **Closest product:** check the current features of commercial simulators before naming one (for
  example, Adacel MaxSim advertises speech recognition). Claim only differences we're sure of:
  deliberate, graded readback errors; designed radio voices; runs in a browser.

## For the judging slot

Keep the server from the recorded run going, with these open on the laptop:
- `/report` for that run;
- the leaderboard on `/viz`;
- `/pipeline`.

Each person's Q&A preparation is in `docs/briefings/`.
