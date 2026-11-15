"""Flight plans (SIM_SPEC §1.8): ADEP/ADES, RFL by the semicircular rule, ASSR."""
import random

from server.sim import flightplan as fp
from server.sim.world import World


def test_semicircular_levels():
    assert fp.semicircular_levels(90, 70, 410) == [
        70, 90, 110, 130, 150, 170, 190, 210, 230, 250, 270, 290, 310, 330, 350, 370, 390, 410]
    assert fp.semicircular_levels(270, 70, 410) == [
        80, 100, 120, 140, 160, 180, 200, 220, 240, 260, 280, 300, 320, 340, 360, 380, 400]
    assert fp.semicircular_levels(179.9, 290, 330) == [290, 310, 330]
    assert fp.semicircular_levels(180.0, 290, 330) == [300, 320]


def test_every_aircraft_has_a_legal_flight_plan(bus, data_dir):
    w = World(bus, data_dir)
    w.load_scenario("busy", 7)
    seen = {}
    for _ in range(2400):
        w.tick(1.0)
        for a in w.aircraft.values():
            seen[a.callsign] = a
    assert len(seen) > 15
    ap = fp.airports(w)["airports"]
    for a in seen.values():
        assert (a.ades if a.flow == "ARR" else a.adep) == "EGKK"
        other = a.adep if a.flow == "ARR" else a.ades
        assert other in ap and other != "EGKK"
        trk, dist = fp.great_circle(ap[a.adep]["lat"], ap[a.adep]["lon"],
                                    ap[a.ades]["lat"], ap[a.ades]["lon"])
        mag = (trk - ap[a.adep]["var"]) % 360
        assert a.rfl in fp.semicircular_levels(mag, 70, 410), (a.callsign, mag, a.rfl)
        assert a.rfl * 100 <= w.perf[a.actype]["ceiling_ft"]
        if dist > fp.LONG_HAUL_NM:
            assert a.wake == "H", a.callsign
        assert len(a.assigned_squawk) == 4 and set(a.assigned_squawk) <= set("01234567")
        assert a.squawk == a.assigned_squawk or a.emergency


def test_departure_squawks_unique_and_in_bank(bus, data_dir):
    w = World(bus, data_dir)
    w.load_scenario("busy", 3)
    for _ in range(1800):
        w.tick(1.0)
    deps = [a for a in w.aircraft.values() if a.flow == "DEP"]
    codes = [a.assigned_squawk for a in deps]
    assert deps and len(codes) == len(set(codes))
    assert all(fp.SQUAWK_BANK[0] <= int(c, 8) <= fp.SQUAWK_BANK[1] for c in codes)


def test_plans_are_seeded(bus, data_dir):
    def plans(seed):
        w = World(bus, data_dir)
        w.load_scenario("busy", seed)
        for _ in range(900):
            w.tick(1.0)
        return sorted((a.callsign, a.adep, a.ades, a.rfl) for a in w.aircraft.values())
    assert plans(11) == plans(11)


def test_demo_aircraft_have_plans_and_request_higher_asks_for_rfl(world, bus):
    for a in world.aircraft.values():
        assert a.adep and a.ades == "EGKK" and a.rfl >= 70
    world.inject("spawn_departure", params={"sid": "BOGNA1X"})
    dep = max((a for a in world.aircraft.values() if a.flow == "DEP"), key=lambda a: a.spawn_t)
    world.pilot_call(dep, "request", what="higher")
    data = bus.of("pilot_call")[-1].data
    assert data["level_fl"] == dep.rfl and data["request"].startswith("climb flight level ")


def test_distance_bands(world):
    rng = random.Random(1)
    short = fp.requested_level(world, "EGKK", "LFPG", "A320", rng)     # ~180 NM, south-east
    long_ = fp.requested_level(world, "EGKK", "LGAV", "A320", rng)     # ~1,300 NM
    assert 160 <= short <= 280 and short % 20 == 10                       # odd (eastbound)
    assert 340 <= long_ <= 410
