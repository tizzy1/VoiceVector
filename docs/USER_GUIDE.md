# VoiceVector — user guide

Everything you can see and do on the three screens. For setup, see the [README](../README.md).

- `/` — controller scope (the trainee's workstation)
- `/viz` — audience view (3D, for a second screen or projector)
- `/report?session=CODE` — read-only session report

---

## Controller scope (`/`)

### Layout
A compact EuroScope-style workstation:
- **Centre:** the 2D radar.
- **Left:** EGKK weather and the selected aircraft.
- **Top:** the departure and arrival lists, docked across the scope.
- **Bottom:** communications and alerts. This dock scrolls independently and resizes from its
  lower-right handle.

Aircraft/weather and the flight lists collapse independently, and the lists resize vertically.
On narrow screens the panes become overlays and the lower dock stacks. Inter and JetBrains Mono
are bundled locally (`web/public/fonts`, OFL licences), so the typography works offline.

### Radar
- **Pan:** drag the scope. **Zoom:** scroll around the cursor, or pick a 10/20/40/80 NM range.
- **Keys** (with the scope focused): arrows pan, `+`/`-` zoom, `C` resets the view.
- **Select:** click a track or choose its callsign. Double-click a track (or use *Centre
  selected*) to centre it.
- **Layers:** each map layer toggles individually.
- **Motion:** positions, altitude and track interpolate between server updates with one
  simulation tick of display delay. Five history dots show recent positions.
- **Vector:** Off, or a 1/2/5-minute projection from current ground speed and track.
- **Tags and labels:** current and cleared level, ground speed, type and assigned targets. Map
  labels and data blocks use deterministic collision spacing. Levels at or below 6,000 ft show
  as A-prefixed altitudes, higher as FL.
- **Pause** shows the authoritative stopped state; reset and reconnect clear old motion history.

### Departure and arrival lists
EuroScope-style lists, one line per aircraft:
- **Departure List:** `FR RWY ACID TYPE W ADEP ADES SID CFL ASSR RFL CLR`
- **Arrival List:** `FR RWY ACID TYPE W ADEP ADES STAR CFL AHDG ASPD DCT ASSR RFL DIST STS`

Click a parameter to edit it; Enter saves, Escape cancels. **An edit is the controller's record,
never a clearance:** it never moves an aircraft. CFL, heading, speed and direct auto-fill from
what the controller says (never from the pilot's readback); a session setting turns auto-fill off.

Each list has filter / sort / reset (`0 F S`), collapse / restore and a resize handle.
- **Filter:** case-insensitive callsign filter, with matching/total counts. It affects the list
  only; the selected aircraft stays selected on the radar.
- **Sort:** session order by default, or by callsign / observed altitude. Sorting never changes
  the simulator's sequence.
- Selecting a radar track highlights its row and clears a filter that would hide it; *Show
  selected* restores a selection hidden by a manual filter.
- Landed and handed-off flights are omitted.
- Selecting an aircraft never opens or moves a panel.

### Selected aircraft
Compares observed level, heading and IAS with their assigned targets, and shows route, next fix,
radio status and relevant alerts.

### Weather (SIMULATED)
The EGKK pane is labelled **SIMULATED**: fixed scenario weather that matches the simulator, not
a live feed. It shows observation time, wind, visibility, cloud, temperature/dew point and QNH,
with an expandable raw METAR. Missing or disconnected weather shows as unavailable.

### Communications and captions
- The Communications pane keeps the latest 200 entries. Partial transcripts update in place,
  with plain-English explanations, instruction chips and error reveals.
- It follows new messages until you scroll back; *Jump to latest* resumes following.
- **Pilot response p50** is the median of the latest ten measured pilot transmissions, from
  server-reported timing (not browser playback latency).
- The bottom caption strip shows the latest three transmissions. Pilot captions follow the audio
  clock when word timings are available, otherwise the whole sentence appears.
- History and samples clear on a session reset or change.

### Traffic and session controls
- Easy / Medium / Busy traffic, 1× / 2× simulation speed, pilot-error rate.
- Selected-aircraft events: emergency, go-around, radio failure.
- Presets change rates without resetting traffic; dedicated traffic scenarios keep their own
  generator. Departures are generated and managed by the simulator.
- *Show/Hide traffic panels* slides the sidebar; Escape returns focus to the toggle.
  Reduced-motion preferences disable the transition.

### Traffic snapshots
Under *Traffic snapshots*, add synthetic demo traffic (default, offline) or a one-time read-only
VATSIM snapshot. Imported aircraft become simulated AI pilots: their motion no longer follows the
external feed. `SNAP` tags and aircraft details show source, timestamps and attribution. The app
never logs in to, transmits on or controls the source network. Details:
[LIVE_IMPORT.md](LIVE_IMPORT.md).

### Pipeline trace (`T`)
*PIPELINE* (or `T`) opens the server-measured stages of each transmission: a drawer with the last
five traces on the controller scope, the latest trace on the audience view. Timings are never
estimated in the browser.

### Browser radio
1. Click **Enable microphone and pilot audio** and allow microphone access.
2. Hold **Space** (outside text fields) or the on-screen **PTT** button to talk; release to send.

There is a microphone meter, playback mute and volume. Blur, a hidden tab or a disconnect releases
PTT; *Disable audio* releases the microphone. Microphone capture works on `localhost`; a remote
controller browser needs a secure (HTTPS) origin.

---

## Audience view (`/viz`)
The same live session as a 3D scene (not a second simulation), for a projector or second device.
- Original box-and-wing aircraft with blueprint edges, true-altitude stems, three-minute fading
  trails and a runway/ILS guide. Symbols are enlarged for visibility; altitudes and positions are
  not exaggerated.
- **Cameras:** *Overview* refits traffic; *Follow radio* holds the speaking pilot for at least six
  seconds; *Chase* follows the selected aircraft; *Departure* follows an active departure; *Free
  orbit* is manual. Drag to pan, right-drag to orbit, scroll to zoom; manual movement releases
  auto-tracking. Transitions take 1.8 s and respect reduced motion.
- Click a model or flight strip to lock the camera and compare current with assigned level,
  heading and IAS.
- *Flights* / *Flight details* toggle the panels; *Labels*, *Trails* and *Airspace* toggle layers.
- **Enable room audio** plays pilot audio to the room without asking for the microphone.
- MapLibre draws the offline background and deck.gl renders local airport and airspace data: no
  map-tile or model downloads. *2D view* is the fallback if 3D fails to start.

### Joining from a second device
Open the address shown in the controller's second-device panel; the session code joins both
screens. If venue Wi-Fi blocks device-to-device traffic, use a shared phone hotspot, or open
`/viz` on an extended display from the same laptop.

---

## Presenter shortcuts
*Demo shortcuts* (or `D`) opens a small presenter box on both views; Escape closes it. Outside
text fields:

| Key | Action |
|---|---|
| `1` | Reset the seeded demo |
| `4` | Arm a wrong-value readback on the next numeric clearance |
| `8` | Pause and generate the session report |
| `J` | Judge mode (larger captions and PTT) |
| `T` | Pipeline trace |

Held keys, modifier shortcuts and typing never trigger demo actions.

---

## Session report
The report shows a score banner and opens a debrief overlay on both views (Escape closes it;
*View report* reopens it). `/report?session=CODE` is a read-only page with the same report.

It contains:
- the score breakdown and findings in categories A–F, with transcript evidence and the correct
  practice;
- the pilot-error catch table and a clickable event timeline;
- coaching points and full-session latency (sample counts, p50, p90).

Missing data shows as unavailable. Both views export JSON. Timeline markers inspect events; they
don't replay audio or aircraft motion. The report stays available while the server keeps the
session; a reset clears it.

---

## Server health
`/api/health` reports `simulation_ready` separately from `ready` (radio readiness) and lists any
missing modules. The simulator runs even without the radio modules; typed input waits until the
radio pipeline is loaded. Restart the server after pulling new modules.

### Audience conflict forecast (`P`, `/viz` only)
The right-hand **CONFLICT FORECAST** panel ranks up to five pairs with at least a 15%
forecast risk. It shows the simulator's 64 seeded futures over 120 seconds, refreshed
every two simulation seconds: probability, time to conflict, and closest separation.
Eight sample paths per aircraft appear in 3D at their simulated altitudes, with a
closest-point marker. Risk at 50% highlights aircraft labels and adds a feed entry once
per threshold crossing. Red means the same pair has an actual STCA loss-of-separation alert.

Press **P** or click **Forecast · P** to toggle the panel and paths together. They start
on and never appear on the controller scope. Missing, stale and disconnected data are
labelled explicitly. Forecasts are advisory and cannot change aircraft or issue clearances.

For the demo, open the controller's **Demo shortcuts · D** and choose **2 Spawn conflict
pair**. Watch the pair on `/viz`, then give a valid separating heading or level clearance
and watch the risk fall. Use the aircraft's displayed callsign; spawned callsigns vary.
