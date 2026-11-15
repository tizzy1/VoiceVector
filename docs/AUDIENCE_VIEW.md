# 3D audience view

`/viz?session=CODE` is a read-only radio audience of the shared simulation; presenter
controls retain their existing server-control capability. Aircraft state and
clearances come from the existing WebSocket. Nothing in the renderer changes flight
decisions or aircraft state. Bhavin owns this folder; Sanju's audio engine is consumed unchanged.

## Rendering

MapLibre GL and deck.gl MapView use the documented reverse-controlled integration:
the deck canvas owns interaction and synchronizes the MapLibre background camera.
MapLibre's worker is bundled by Vite. The initial map is an offline graphite style;
local EGKK geometry supplies runway, airspace, procedure paths and reference rings.
No external tiles, API key or downloaded 3D model is needed.

Aircraft use original procedural solid/wireframe box-and-wing meshes. Geographic
positions follow the simulator projection (`x` NM east, `y` NM north); elevation
converts feet to metres. Heading rotates the nose, while chase bearing uses track.
Aircraft symbols are enlarged with zoom for readability, explicitly labelled;
altitude anchors and labels remain at the actual simulated altitude. The illustrative
approach guide is a 3-degree ramp from the active runway's recorded elevation.

Motion uses the existing one-tick interpolation buffer. Trails retain 180 sim
seconds, with bounded per-aircraft history and expiry on removal, reset or loss of
state. Busy scenes show fewer detailed labels; selection and the flight rail expose
the full status. Speech bubbles and the shared strip use supplied audio word clocks.

## Camera and interaction

- Overview: fit the airport and active traffic. Click again to refit.
- Lock selected: smooth follow at aircraft altitude, with live comparison details.
- Chase: higher-pitch view behind the aircraft along its ground track.
- Follow radio: retain a pilot for at least six seconds before switching. The
  inspector and selection highlight follow the camera target.
- Departure: follow an active departure; disabled when none exists.
- Free orbit: drag/right-drag/scroll without auto-follow fighting the user. Escape
  releases automatic camera movement. Selecting another model locks again.

Transitions are 1.8 seconds. Reduced-motion preference removes animated travel.
Disappearing/landed targets return locked/chase cameras to overview. Report overlays
keep their native modal keyboard/focus behavior. On smaller screens, the flight
rail and details can be opened separately; selecting a flight closes the mobile rail.

## Verification and fallback

Use `node --test web/tests/viz.test.mjs` for projection, mesh/heading, trail lifetime,
camera transitions, speaker dwell and token-preserving speech wrapping.

Run `.venv/bin/python tools/viz_load_fixture.py --port 8002` for an isolated moving
40-aircraft layout. Open `http://localhost:8002/viz`. The scene's `data-render-fps`
attribute reports actual deck render-callback frequency; it is a local diagnostic,
not a published hardware-independent performance claim. This fixture has no radio
or rules factories and does not affect the normal port-8000 session.

Visual checks include direct model picking, lock/chase/free camera modes, panel
toggles, narrow viewport overflow, room-audio controls, 2D fallback/retry and report
delivery. Physical second-device audio/Wi-Fi behavior still needs venue rehearsal.
The shared 2D Radar is the fallback for graphics initialization errors, and a manual
2D button allows preflight testing. Closing/retrying disposes GPU contexts, frame
loops and observers.

Source: [deck.gl MapLibre integration](https://deck.gl/docs/developer-guide/base-maps/using-with-maplibre),
[SimpleMeshLayer](https://deck.gl/docs/api-reference/mesh-layers/simple-mesh-layer).
