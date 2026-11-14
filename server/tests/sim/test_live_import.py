"""Read-only live-traffic snapshot import (SIM_SPEC §3.7) — synthetic VATSIM v3 documents."""
import math
from datetime import datetime, timedelta, timezone

from server.sim import live_import as li
from server.sim.world import World

NOW = datetime(2026, 11, 14, 22, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def empty(bus, data_dir):
    w = World(bus, data_dir)
    w.load_scenario("custom", 1)
    w.settings = w.settings.model_copy(update={"arr_per_hr": 0, "dep_per_hr": 0})
    return w


def at(w, x, y):
    return w.proj.to_latlon(x, y)


def pilot(w, cs, x, y, alt, gs=250, hdg_mag=0, actype="A320", dep="LEMD", arr="EGKK",
          route="", age_s=5):
    lat, lon = at(w, x, y)
    return {"cid": 1234567, "name": "Real Person EGLL", "callsign": cs, "server": "UK",
            "pilot_rating": 0, "latitude": lat, "longitude": lon, "altitude": alt,
            "groundspeed": gs, "transponder": "4521", "heading": hdg_mag, "qnh_mb": 1013,
            "logon_time": iso(NOW - timedelta(hours=1)),
            "last_updated": iso(NOW - timedelta(seconds=age_s)),
            "flight_plan": {"flight_rules": "I", "aircraft_short": actype, "departure": dep,
                            "arrival": arr, "route": route}}


def doc(pilots, age_s=10):
    return {"general": {"version": 3, "update_timestamp": iso(NOW - timedelta(seconds=age_s))},
            "pilots": pilots, "controllers": [], "atis": []}


def final_point(w, d, offset=0.0):
    c = math.radians(w.loc_course)
    return (-d * math.sin(c) + offset * math.cos(c), -d * math.cos(c) - offset * math.sin(c))


def mag(w, true_hdg):
    return true_hdg - li._mag_variation(w)


def test_normalise_drops_personal_data(bus, data_dir):
    w = empty(bus, data_dir)
    snap = li.normalize_vatsim(doc([pilot(w, "BAW123", 0, -25, 9000)]), NOW)
    [t] = snap["targets"]
    assert snap["source"] == "vatsim" and snap["fetched_at"].startswith("2026-11-14T22:00")
    assert "cid" not in t and "name" not in t and "Real Person" not in str(snap)
    assert t["callsign"] == "BAW123" and t["arr"] == "EGKK" and t["actype"] == "A320"


def test_import_classifies_filters_and_reports(bus, data_dir):
    w = empty(bus, data_dir)
    fx, fy = final_point(w, 9.0)
    pilots = [
        pilot(w, "BAW123", 0, -30, 9000, hdg_mag=mag(w, 0)),          # arrival, far: to a stack
        pilot(w, "EZY45AB", fx, fy, 2900, gs=170, hdg_mag=mag(w, w.loc_course)),   # on final
        pilot(w, "RYR88", 5, 8, 4000, dep="EGKK", arr="EIDW", route="NOVMA1X NOVMA L620"),
        pilot(w, "SAS4421", 10, 10, 9000, dep="ESSA", arr="EGLL"),    # overflight
        pilot(w, "DLH9", 60, 0, 9000),                                 # out of area
        pilot(w, "VLG77", 0, -20, 22000),                              # above the band
        pilot(w, "GABCD", 1, 1, 0, gs=0, arr="EGKK"),                   # on the ground
        pilot(w, "TOM5", 0, -15, 7000, age_s=600),                     # stale target
        pilot(w, "QTR9", 3, -22, 8000, actype="A35K"),                 # mapped type
    ]
    status = w.import_live(li.normalize_vatsim(doc(pilots), NOW))
    assert status["ok"] and not status["stale"] and status["error"] is None
    assert {i["callsign"] for i in status["imported"]} == {"BAW123", "EZY45AB", "RYR88", "QTR9"}
    reasons = {s["callsign"]: s["reason"] for s in status["skipped"]}
    assert reasons == {"SAS4421": "not_gatwick_traffic", "DLH9": "out_of_area",
                       "VLG77": "out_of_level_band", "GABCD": "on_ground", "TOM5": "stale"}
    assert status["counts"]["imported"] == 4 and status["counts"]["skipped"] == 5
    assert "VATSIM" in status["attribution"] and status["source_time"]

    baw, ezy, ryr, qtr = (w.aircraft[c] for c in ("BAW123", "EZY45AB", "RYR88", "QTR9"))
    assert baw.flow == "ARR" and baw.lat_mode == "LNAV" and baw.route[-1] in ("WILLO", "TIMBA")
    assert baw.cleared_alt == 9000 and baw.ias <= 250 and abs(baw.hdg) % 360 < 1
    assert ezy.lat_mode == "LOC" and ezy.established and ezy.approach_cleared
    assert ryr.flow == "DEP" and ryr.route_name == "NOVMA1X" and ryr.lat_mode == "LNAV"
    assert ryr.cleared_alt == 6000 and ryr.on_frequency
    assert qtr.actype == "B789" and qtr.wake == "H"
    kinds = [e.data["kind"] for e in bus.of("pilot_call")]
    assert kinds.count("checkin") == 4
    ev = bus.of("inject")[-1].data
    assert ev["kind"] == "live_import" and len(ev["imported"]) == 4 and ev["skipped"] == 5


def test_imported_aircraft_are_ordinary_simulated_traffic(bus, data_dir):
    w = empty(bus, data_dir)
    fx, fy = final_point(w, 9.0)
    w.import_live(li.normalize_vatsim(doc([
        pilot(w, "EZY45AB", fx, fy, 2900, gs=170, hdg_mag=mag(w, w.loc_course)),
        pilot(w, "BAW123", 0, -30, 9000, hdg_mag=mag(w, 0))]), NOW))
    assert {c.icao for c in w.callsigns()} == {"EZY45AB", "BAW123"}
    for _ in range(400):
        w.tick(1.0)
    assert [e.callsign for e in bus.of("landing")] == ["EZY45AB"]
    assert w.aircraft["BAW123"].lat_mode in ("LNAV", "HOLD")


def test_conflicting_real_traffic_is_imported_and_alerts(bus, data_dir):
    w = empty(bus, data_dir)
    w.import_live(li.normalize_vatsim(doc([
        pilot(w, "AAA1", -1.0, -20, 6000, hdg_mag=mag(w, 90)),
        pilot(w, "BBB2", 1.0, -20, 6000, hdg_mag=mag(w, 270))]), NOW))
    assert set(w.aircraft) == {"AAA1", "BBB2"}
    w.tick(1.0)
    assert any(a.kind == "STCA_LOS" for a in w.public_state().alerts)


def test_snapshot_age_callsign_clash_mode_and_empty(bus, data_dir):
    w = empty(bus, data_dir)
    old = w.import_live(li.normalize_vatsim(doc([pilot(w, "BAW1", 0, -25, 9000)], age_s=900),
                                            NOW))
    assert not old["ok"] and old["error"] == "snapshot_too_old" and not w.aircraft
    stale = w.import_live(li.normalize_vatsim(doc([pilot(w, "BAW1", 0, -25, 9000)], age_s=90),
                                              NOW))
    assert stale["ok"] and stale["stale"]
    again = w.import_live(li.normalize_vatsim(doc([pilot(w, "BAW1", 0, -25, 9000)]), NOW))
    assert again["skipped"] == [{"callsign": "BAW1", "reason": "callsign_in_use"}]
    assert w.import_live([], source="vatsim")["error"] == "empty"
    ctx = w.import_live(li.normalize_vatsim(doc([pilot(w, "X1", 0, -25, 9000)]), NOW),
                        mode="context")
    assert ctx["error"] == "mode_not_supported"


def test_unknown_airline_is_spelled_and_types_map(bus, data_dir):
    w = empty(bus, data_dir)
    assert w.spoken("SAS4421") == "sierra alpha sierra four four two one"
    perf = w.perf
    assert li.map_type("B38M", perf) == ("B738", False)
    assert li.map_type("A320", perf) == ("A320", True)
    assert li.map_type("ZZZZ", perf) == ("A320", False)
    assert li.map_type("C152", perf) == ("C172", False)


def test_magnetic_heading_is_converted_to_true(bus, data_dir):
    w = empty(bus, data_dir)
    var = li._mag_variation(w)
    assert 0 <= var < 3
    w.import_live(li.normalize_vatsim(doc([pilot(w, "BAW7", 0, -30, 9000, hdg_mag=100)]), NOW))
    assert math.isclose(w.aircraft["BAW7"].hdg, 100 + var, abs_tol=1e-6)
