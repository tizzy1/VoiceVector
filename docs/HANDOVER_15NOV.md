# Handover: changes made by Sanju in Tudor's and Bhavin's areas (15 Nov 2026, night)

**Why this exists.** On the night of 14–15 Nov, Tudor was asleep and Bhavin had run out of Claude
usage, with the deadline at 11:30. Sanju took on their open items from Tudor's playtest notes
(15 Nov 02:10 and 02:29) and asked for every change to be documented. The git rules say to stop
and tell Sanju before editing someone else's files; Sanju is the one who authorised this. Nothing
here changes an existing contract field; the contract additions are listed explicitly below.

**Where it lives.** Branch `sanju/team-sim`, **not merged into `main`**. The ownership hook
blocks these files, so the commits were made with the hook skipped (`--no-verify`), on Sanju's
decision, and only pushed as a branch for Tudor and Bhavin to review and merge. To review:
`git fetch && git log --stat main..origin/sanju/team-sim`; to take it: `git merge --ff-only
origin/sanju/team-sim` (all tests pass on the branch).

Each item says what changed, why, where, how it is tested, and how to undo it. Commit messages
say "(Sanju for Tudor)" or "(Sanju for Bhavin)", and code comments carry "15 Nov (Sanju for …)".

---

## 1. Simulator and pilot (Tudor's area)

| # | Playtest note | What changed | Files |
|---|---|---|---|
| S1 | One aircraft for judges to land, holding at 5,000 ft | New scenario `judge`: BAW117 (A320, crisp pilot) holding at **TIMBA** at 5,000 ft, checks in on load; no arrivals or departures ever spawn. TIMBA rather than WILLO because TIMBA is 13 NM out and 15 NM south of the 26L final (a short vector), whereas WILLO is behind the threshold and needs a long downwind. | `server/sim/world.py` (`_load_judge`, `JUDGE_HOLD_FIX`, `JUDGE_ALT_FT`), `server/sim/traffic.py`, `server/sim/runway.py` (rates 0 for `judge`) |
| S2 | Descend to 2,000 ft gave "say again the altitude" | The 2,000 ft area (placeholder until real minimums are in) was a ±3 NM, 15 NM strip on the centreline, so base-leg aircraft got the 3,000 ft general minimum. Now **16 NM out, ±8 NM wide**. 20 NM out is still refused (Tudor's existing tests). The pilot now always says why (see V5 below). | `server/sim/airspace.py` |
| S3 | 200 kt "too slow" at 3,000 ft about to establish | Minimum speed was clean speed (A320 205 kt) everywhere until established. Now, at or below 5,000 ft, or cleared for / intercepting the approach: `min(clean, max(Vapp+15, 160))` (A320: 160 kt). Established: Vapp+5 (unchanged). Higher up: clean (unchanged). | `server/sim/evaluate.py` (`APPROACH_CONFIG_BELOW_FT`, `APPROACH_MIN_KT`) |
| S4 | Approach clearance without a heading | An aircraft in a hold or on its STAR that is cleared for the approach with no new heading now leaves the hold/route **on its present heading** until it captures the localiser. A heading in the same call still wins. | `server/sim/registry.py` (`_approach`) |
| S5 | Turn + ILS: only the turn (or only "unable") was acknowledged | When one instruction is refused, the pilot now **reads back and flies the accepted ones** and refuses the rest with the reason: "Speedbird 117, left heading 290, unable, too high for the glideslope". Previously nothing at all was applied. Tudor's test `test_unable_has_reason_and_no_apply` was updated to the new behaviour (renamed `…_applies_only_the_accepted_part`). | `server/pilot/engine.py`, `server/pilot/readback.py` (`_partial_refusal`), `server/tests/pilot/test_engine_stub.py` |
| S6 | Mistakes should be small (missed instructions, wrong numbers) | Error kinds are no longer tried uniformly. A weighted order puts adjacent/swapped numbers and omitted elements first; level-type confusion, turn-direction swaps and level busts stay possible but rarer (`KIND_WEIGHTS`). Rates (`RATE`) are unchanged. | `server/pilot/errors.py` (`KIND_WEIGHTS`, `weighted_order`), `server/pilot/engine.py` |
| S7 | Spawn at different stages, including on final | **Random-traffic presets** (easy / medium / busy / ramp) now spawn arrivals at a random stage: STAR 55 %, hold 15 % (7,000–10,000 ft, a free level in the stack), downwind 15 % (5,000 ft, 7 NM abeam), final 15 % (10–12.5 NM, on the glidepath, cleared ILS). `custom`, `demo` and `judge` are unchanged. Instructor injects can ask for one: `spawn_arrival` with `params.stage` = `star`/`hold`/`downwind`/`final`. | `server/sim/traffic.py` (`_restage`, `STAGE_WEIGHTS`), `server/sim/world.py` (`inject`) |
| S8 | Start with few aircraft, build up to ~8 | Random-traffic presets allow 2 active arrivals at the start, one more every 2 minutes, at most 8. The backlog is capped at 2 so it does not flood when space frees up. | `server/sim/traffic.py` (`MAX_ACTIVE_ARRIVALS`, `START_ACTIVE_ARRIVALS`, `BUILD_UP_S`) |
| S9 | Dep/Arr lists empty, editing unavailable | The simulator computed flight plans and strips but never published them. `_public()` now fills `flight_rules`, `adep`, `ades`, `rfl`, `assigned_squawk`, `selected_level` (the aircraft's real cleared level, like Mode S), `distance_to_touchdown_nm` (track miles: route fixes, or along the localiser) and `strip` (`strips.view`). Bhavin's lists and `edit_strip` control were already wired, so editing now works. | `server/sim/world.py` (`_public`, `_track_distance`) |
| S10 | `QUERY_FIX_PASSED` (contract item) | `evaluate` now returns `QUERY_FIX_PASSED` instead of the interim `QUERY_UNKNOWN_FIX` + `what=fix_passed`. The pilot's words are unchanged ("Confirm direct TUFOZ, we've passed it"). | `server/sim/evaluate.py` |
| S11 | Serialization warnings in the rehearsal | `errors.mutate` wrote ints into `Instruction.value` (`float | str`); now floats. No warnings left in the test run. | `server/pilot/errors.py` |

Tests: `server/tests/sim/test_team_15nov.py` (judge hold + a full vector-to-land, lists, 2,000 ft,
approach speeds, partial refusal, `QUERY_FIX_PASSED`, build-up cap and stages, stage inject,
error weighting, float values).

**For judges (S1):** load "Judge: land one aircraft". BAW117 checks in holding at TIMBA, 5,000 ft.
A sequence that lands it in about 8 minutes:
1. "Speedbird one one seven, turn left heading three six zero, descend altitude three thousand feet."
2. When it is about 5 NM from the extended centreline: "Speedbird one one seven, turn left heading two niner zero, cleared ILS approach runway two six left."
It captures the localiser beyond 9 NM, then the glideslope from below, and lands. Cleared too late
(localiser inside ~8 NM at 3,000 ft) it stays above the glideslope and goes around, which is
realistic.

---

## 2. Contract additions (Bhavin's files)

| Change | Where |
|---|---|
| `SessionSettings.scenario` accepts `"judge"` | `server/contracts.py`, mirrored in `web/src/protocol.ts` |

Additive only: every existing value still validates.

---

## 3. Voice (Sanju's own area, for context)

Done in the same session; listed so the playtest notes can be ticked off in one place.

| # | Playtest note | Fix |
|---|---|---|
| V1 | "Turn left 240" read back twice | Scribe's "turn left 240 degrees" was a relative turn of 240°; Claude then added "heading 240". Relative turns are now ≤ 90°, and the Claude merge adds at most one instruction per family. |
| V2 | ILS clearance not registered | "I.L.S." (dotted) and runways without the word "runway" ("cleared ILS 26 left") are understood. |
| V3 | Pilots repeat the previous instruction | "Readback correct" / "affirm" was treated as "read back" and re-read the last clearance; now no reply. Duplicate instructions in one call are read back once. |
| V4 | Bare "expecting" | Critical expectations (approach/runway, level) are read back in full; "expect vectors" or no detail → "Roger, Speedbird 117" (or left out alongside other instructions). |
| V5 | Vague queries | When the simulator gives numbers, the pilot says them: "Unable altitude 2000, minimum altitude here is 3000". |

---

## 4. UI (Bhavin's area)

See section 5 once the UI items are in (filled in as they land).

---

## 5. Not done / needs the owner

Filled in at the end of the session.
