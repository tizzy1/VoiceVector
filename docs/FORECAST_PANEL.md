# Conflict forecast — side panel on the audience view (brief for Bhavin)

From Tudor, 15 Nov. Supersedes the placement in `ASSIGNMENT_BHAVIN.md` §11 and `SIM_SPEC.md` §3.6
("show risk on tags and paths on radar/viz").

## Decision
Show the probabilistic conflict forecast **only on the audience view** (`/viz`, the 3D second
window), as a **side panel plus 3D path fans**. Do **not** show it on the controller scope (`/`).

Why: the scope is the trainee's realistic workstation. A real Director has STCA, not a
probability read-out, and the trainee should spot conflicts themselves. The audience view exists
to explain what is going on, and "the system is simulating 64 futures per aircraft" is exactly the
kind of thing judges should see there.

The controller scope keeps what it has: the deterministic STCA alerts (red) and wake alerts
(amber). Nothing forecast-related is drawn on it.

## What the sim already provides
`server/sim/forecast.py` runs inside `World.tick`, every 2 sim seconds. For each nearby pair of
aircraft it runs 64 seeded futures over the next 120 s (5 s steps), varying pilot reaction time,
wind, turn and climb rates, speed and level-off overshoot. It is advisory: it never changes an
aircraft, an alert or a finding, and it uses the controller's intended clearances (never a planned
pilot error). Cost: about 6 ms per pass in busy traffic, 19 ms with 40 aircraft.

Today it is only available in Python as `world.forecasts`. It is **not on the wire** yet.

## Step 1 — contract (you, one commit with `protocol.ts`)
Additive and optional:
```python
class ForecastPath(BaseModel):
    callsign: str
    points: list[tuple[float, float, float]]   # (lat, lon, alt_ft) every 15 s, t = 0..120 s (9 points)

class ConflictForecast(BaseModel):
    pair: tuple[str, str]
    p_conflict: float              # 0..1: share of the 64 futures that lose separation (< 3 NM and < 1,000 ft)
    t_conflict_s: Optional[float]  # median seconds until the conflict starts (None if p is 0)
    min_sep_nm: float              # median closest horizontal distance
    min_sep_ft: float              # median vertical separation at that moment
    rollouts: int = 64
    horizon_s: int = 120
    computed_t: float              # sim time of this forecast
    samples: list[ForecastPath] = Field(default_factory=list)   # 8 sample futures per aircraft, only when p >= 0.15

# StateMsg: add
    forecasts: list[ConflictForecast] = Field(default_factory=list)   # pairs with p >= 0.01, highest first
```
Tell Tudor when it is merged. He then fills `StateMsg.forecasts` from `world.forecasts` (adding
altitude and lat/lon to the sample paths), about 20 minutes of work. Until then the list is empty
and the panel shows its empty state.

## Step 2 — the panel (`/viz` only)
A right-hand side panel, same industrial styling as the rest of the audience view.

**Title bar:** `CONFLICT FORECAST` with a small `ADVISORY` chip. Sub-line: `64 simulated futures
per aircraft · next 2 min · updated every 2 s`.

**One row per pair**, sorted by probability, at most 5 rows:
```
BAW117 ↔ EZY45AB        62%   ▓▓▓▓▓▓░░░░   in 1:20   closest 1.8 NM / 300 ft
```
- Callsigns in plain form where there is room ("British Airways 117").
- Probability as a number and a bar.
- Time to conflict as m:ss from `t_conflict_s`.
- Closest approach from `min_sep_nm` / `min_sep_ft`.
- Colour: under 15% not listed; 15–49% dim amber; 50% and over bright amber; if the same pair also
  has an `STCA_LOS` alert in `state.alerts`, red with the label `LOSS OF SEPARATION`.
- Rows fade in and out (180 ms); a pair that drops below 15% fades out, so the audience sees the
  risk collapse when the controller fixes it.

**Empty state:** `No conflicts forecast in the next 2 minutes`, so the panel is never blank.

**Footer, small:** `Forecast, not an alert. The controller does not see this.`

## Step 3 — in the 3D scene
For every forecast with samples (p ≥ 0.15):
- Draw each aircraft's 8 sample futures as thin translucent amber paths at true altitude
  (deck.gl `PathLayer`, alpha about 0.15, points are lat/lon/alt). They fan out from the aircraft:
  the "spaghetti".
- A small pulsing marker where the two fans come closest (use the midpoint of the closest pair of
  points across the two aircraft's samples).
- Highlight both aircraft's labels while their pair is at 50% or more.
- When a pair first crosses 50%, add one line to the event feed:
  `Forecast: BAW117 and EZY45AB 62% conflict risk in 1:20`.
- Optional: clicking a row flies the camera to that pair (same 1.6–2.4 s `flyTo`).

## Controls
- Hotkey `P` on the audience view toggles the panel and the fans together.
- Default: on.
- No toggle, button or drawing on the controller scope.

## Behaviour to check
1. Demo beat 2 (spawn the conflict pair): the pair appears in the panel within about 4 s, the fans
   are visible, and the probability climbs towards 100%.
2. Tudor turns or descends one aircraft by voice: within a few seconds the probability drops and
   the row and fans fade out.
3. No forecasts: the empty state shows.
4. A pair with an actual loss of separation shows red in the panel (the scope shows its normal
   STCA alert).
5. The controller scope shows nothing forecast-related at any time.
6. No frame drops in the 3D view with 5 pairs and their fans on screen.
7. Numbers are only what the server sent. Nothing is computed or estimated in the browser except
   the m:ss formatting and the closest-point marker.

## Pitch line
"On the second screen you can see what the simulator sees: 64 possible futures for every aircraft,
every two seconds, and the chance two of them meet. The trainee doesn't get that help. Watch it
collapse when I turn Speedbird."

## If time is short
Cut in this order: camera fly-to on click → closest-point marker → event feed line → 3D fans
(keep the panel). The panel alone still makes the point.
