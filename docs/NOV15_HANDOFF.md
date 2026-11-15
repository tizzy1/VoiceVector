# 15 November integration handoff

Bhavin's platform/UI now routes `edit_strip {callsign, field, value}` to
`World.edit_strip`, broadcasts refreshed state, and validates rejection without changing
flight targets. `set_strip_autofill {on: bool}` synchronizes the optional
`SessionSettings.strip_autofill` (default true), survives scenario reload, and sets
`world.strip_autofill`. Both Python and TypeScript contracts are updated together.

The controller has docked horizontal Departure/Arrival lists with inline editors,
filter/sort/reset, independent collapse/close/restore and vertical resize. Selecting a
track only changes selection; it does not open any panel. List CFL and radar tag CFL
use the controller's `strip.cfl` in feet. Mode S `selected_level` is separate and amber
when it differs. RFL is a flight-level number; squawks remain strings. Unknown fields
show a dash. Editing waits for the simulator to publish its flight-plan fields; no
optimistic list value is presented as server-confirmed.

The PIPELINE button / T shortcut works on both views. Controller: right overlay and
five-transmission history. Audience: bottom-left, latest trace only. Provisional updates
replace their transaction; final records lock. Reset/disconnect clears history. The UI
uses only supplied stage times and totals, never estimates provider speed. Empty state
explicitly waits for the producer. Fields/detail remain available for keyboard readers.
Typing T in an input does not toggle the panel. Escape/close restores button focus.

## Tudor still needs to supply

- `World._public`: `flight_rules`, `adep`, `ades`, `rfl`, `assigned_squawk`,
  `selected_level=ac.cleared_alt` (feet), `strip=world.strip(ac.callsign)`.
- `AircraftPublic.distance_to_touchdown_nm`: actual remaining track distance, for DIST
  and arrival sequencing. This optional field is now in both contracts. Unknown is
  `None`; do not substitute straight-line distance for remaining route distance.
- `World.edit_strip`: emit `strip_edit` with field/value/by once per accepted edit.
  Current implementation stores the record but emits no edit event. Session routing
  deliberately does not duplicate the simulator-owned event.
- Switch the passed-fix evaluation fallback (`QUERY_UNKNOWN_FIX`, what=fix_passed) to
  the now-supported `QUERY_FIX_PASSED` code.

## Sanju still needs to supply

- Emit actual `TraceMsg` provisional/final records, exact stage names/categories from
  VOICE_PIPELINE §20.1 (plus blocked when appropriate), server monotonic timing and
  total time to playback release. Respect planned-error reveal rules in detail fields.
- Append final traces to the run's `traces.jsonl`, fail open, and verify 20 consecutive
  transmissions, final uniqueness and machine timing agreement with latency chips.
- Investigate the serializer warnings in `server/tests/voice/test_rehearsal.py`:
  numeric `Instruction.value` updates are serialized with a stale expected string type.
  All tests currently pass; these warnings are not suppressed by the platform.

## Validation

Real-world integration tests cover accepted/rejected edits, no movement from strip
changes, setting synchronization on two viewers and reset persistence. Browser checks
use an isolated fixture that publishes real simulator strip values and explicitly
labelled synthetic trace messages, without provider API calls. A synthetic waterfall
is UI verification, not a measured latency result. Actual production data will appear
when the owner producers above are implemented.
