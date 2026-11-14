from server.contracts import Event, Report, RulesAPI
from server.rules.engine import RulesEngine


def test_report_is_valid(world, bus, data_dir):
    rules = RulesEngine(world, bus, data_dir)
    _: RulesAPI = rules
    bus.subscribe(rules.on_event)
    bus.emit(Event(t=1, type="ctrl_transmission", actor="CONTROLLER", callsign="BAW117"))
    world.inject("mayday", "DLH4KA")
    for _ in range(10):
        world.tick(1.0)
        rules.on_tick(world.snapshot())
    rep = rules.report("ABCD")
    Report.model_validate(rep.model_dump())
    assert rep.session_id == "ABCD" and rep.duration_s == 10 and rep.score == 100
    assert rep.stats["transmissions"] == 1
    assert [e.type for e in rep.timeline] == ["inject"]
    assert rep.narrative is None
