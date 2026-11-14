Imperial AI Hackathon 2026

# SweatboxAI

Gatwick air traffic control training with AI pilots. The server, controller radar
and second screen share one session and the interfaces in
`server/contracts.py` and `web/src/protocol.ts`.

## Run locally

Use Python 3.12 and Node.js 20.19+ or 22.12+.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cd web
npm ci
npm run build
cd ..
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 for the controller. On a second device on the same
network, open the address shown in the second-device panel. The session code
joins both screens to the same server. If venue Wi-Fi blocks device connections,
use a shared hotspot or open /viz on an extended display.

The foundation view serves the Gatwick map, session and connection status,
caption feed and typed input. /viz currently shows the same 2D foundation.
Drag the scope to pan, scroll to zoom around the cursor, or choose a
10/20/40/80 NM range. Map layers can be toggled individually. Click a track or
choose its callsign to select it; double-click a track or use Centre selected
to centre it. With the scope focused, arrows pan, +/- zoom and C resets the view.
Aircraft positions, altitude and track interpolate between server updates using
one simulation tick of display delay. Five history dots show recent server
positions. The Vector control selects Off or a 1/2/5-minute projection using
current ground speed and track. Pause displays the authoritative stopped state;
reset and reconnect clear old motion history.
The selected-aircraft pane compares observed level, heading and IAS with their
assigned targets and shows route, next fix, radio status and relevant alerts.
Selecting a track opens the sliding right sidebar and reveals its flight strip.
Session settings are collapsible beneath the operational panels.
Arrival and departure flight strips share selection with the radar and its
aircraft dropdown. Departures are grouped as queued, taking off and airborne.
The sidebar provides Easy/Medium/Busy traffic targets, 1×/2× simulation speed,
pilot-error rate and selected-aircraft emergency, go-around and radio-failure
events. Presets update demo/custom rates without resetting traffic; dedicated
traffic scenarios retain their own generator policy. Speech depends on the
radio pipeline. Departure generation remains a simulator integration task.
Use Show/Hide traffic panels to slide the sidebar from the right. Close panels
or Escape returns focus to the toggle; reduced-motion preferences disable the
transition.
Lists preserve server order, omit landed/handed-off flights and show current
level, cleared level, ground speed, aircraft type, wake category and route.
Tags show current and cleared level, ground speed, type and assigned targets.
Altitude 6,000 ft and below uses A-prefix labels; higher levels use FL.
The shared caption strip retains the latest three transmissions, updates partials
in place, and displays parsed chips, plain-English summaries and supplied latency
metrics. Pilot captions currently display the full sentence; word timing awaits
the browser audio integration.
3D, browser audio integration, camera controls and the report UI are subsequent
tasks. /report currently serves the foundation application.

Tudor's simulator now runs independently of the radio pipeline. Both views
show live aircraft, with Pause/Resume and Reset demo controls. /api/health
reports simulation_ready separately from ready (radio transmission readiness),
and lists missing modules. Typed input stays disabled until the radio pipeline
is loaded. Narrative generation is optional for the deterministic report.
Restart the server after pulling new modules. Production loads exactly:

- server.sim.world.World
- server.pilot.engine.PilotEngine
- server.rules.engine.RulesEngine
- server.pilot.readback.Composer
- server.voice.pipeline.VoicePipeline
- server.agents.narrative.Narrative

Construction follows docs/CONTRACTS.md. The server uses the available team modules and reports missing capabilities. A typed clearance is routed to
VoicePipeline.on_typed and resulting captions/state are broadcast to both views.

For web development, run `npm run dev` in web/ alongside the Python server.
Vite proxies /api and /ws to port 8000.

## Verify

```sh
python -m pytest server/tests/integration
python server/tests/integration/smoke_test.py
cd web && npm run build
```

Radar coordinate and formatting regression checks use the existing TypeScript dependency:

```sh
node --test web/tests/*.test.mjs
```

The default smoke test starts an actual TCP server and connects two WebSocket
clients using isolated test doubles under server/tests/integration. After the
team modules arrive, run `python server/tests/integration/smoke_test.py --real`
to verify the actual typed-command round trip. The real-mode timeout is 15 seconds
to include readback duration and pilot application delay. Test doubles retain
the 5-second limit. Override either with --timeout SECONDS. This checks target
application, not completion of the aircraft turn.

The ownership hook is installed separately in each clone:

```sh
sh tools/git/install-hook.sh bhavin
```

Use the corresponding owner name on Tudor's or Sanju's machines. Follow the
named assignments and Git rules under docs/. Keep API keys in the ignored .env
file. Events are written to ignored runs/<id>/events.jsonl.

## Data credits

The supplied Gatwick geometry and procedures derive from the VATSIM UK Sector
File (GPL-3.0) and OurAirports. Aircraft performance values derive from OpenAP.
Source notes and simulation assumptions are retained in data/. The current
radar draws the supplied geometry directly.
