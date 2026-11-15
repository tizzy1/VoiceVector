from fastapi.testclient import TestClient

from server.main import create_app
from server.pilot.engine import PilotEngine
from server.rules.engine import RulesEngine
from server.sim.world import World

FACTORIES = {"world": World, "pilot": PilotEngine, "rules": RulesEngine}


def receive_until(viewer, predicate):
    for _ in range(40):
        message = viewer.receive_json()
        if predicate(message):
            return message
    raise AssertionError("Expected message did not arrive")


def test_settings_update_real_simulator_and_both_clients(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            with client.websocket_connect("/ws?role=viz") as viz:
                radar.send_json({"type": "control", "action": "set_traffic",
                                 "params": {"arr_per_hr": 26, "dep_per_hr": 20}})
                for viewer in (radar, viz):
                    settings = receive_until(viewer, lambda m: m["type"] == "session"
                                             and m["settings"]["arr_per_hr"] == 26)["settings"]
                    assert settings["dep_per_hr"] == 20
                world = client.app.state.session.world
                assert world.generator.rate_per_hr() == 26
                assert len(world.aircraft) == 5
                for action, params, key, value in (
                    ("set_speed", {"sim_speed": 2}, "sim_speed", 2),
                    ("set_error_rate", {"error_rate": "high"}, "error_rate", "high"),
                ):
                    radar.send_json({"type": "control", "action": action, "params": params})
                    receive_until(radar, lambda m: m["type"] == "session"
                                  and m["settings"][key] == value)
                assert world.settings.sim_speed == 2
                assert client.app.state.session.pilot.error_rate == "high"


def test_emergency_targets_selected_aircraft_without_voice(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            radar.send_json({"type": "control", "action": "inject",
                             "params": {"kind": "mayday", "callsign": "BAW117",
                                        "params": {"reason": "engine failure"}}})
            state = receive_until(radar, lambda m: m["type"] == "state"
                                  and any(a["emergency"] for a in m["aircraft"]))
            marked = [a for a in state["aircraft"] if a["emergency"]]
            assert [a["callsign"] for a in marked] == ["BAW117"]
            assert marked[0]["squawk"] == "7700"


def test_invalid_speed_is_rejected_without_changing_settings(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            radar.send_json({"type": "control", "action": "set_speed",
                             "params": {"sim_speed": 3}})
            receive_until(radar, lambda m: m["type"] == "caption"
                          and "speed must" in m["text"])
            assert client.app.state.session.settings.sim_speed == 1


def test_strip_edit_routes_to_world_without_changing_flight_targets(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect('/ws?role=radar') as radar:
            world = client.app.state.session.world
            before = world.aircraft['BAW117'].cleared_alt
            radar.send_json({'type':'control','action':'edit_strip',
                'params':{'callsign':'BAW117','field':'cfl','value':'F70'}})
            receive_until(radar,lambda m:m['type']=='session')
            assert world.strip('BAW117')['cfl'] == 7000
            assert world.aircraft['BAW117'].cleared_alt == before
            radar.send_json({'type':'control','action':'edit_strip',
                'params':{'callsign':'BAW117','field':'assr','value':'9999'}})
            receive_until(radar,lambda m:m['type']=='caption' and 'rejected' in m['text'])
            assert world.aircraft['BAW117'].cleared_alt == before


def test_strip_autofill_setting_syncs_and_survives_demo_reset(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect('/ws?role=radar') as radar:
            with client.websocket_connect('/ws?role=viz') as viz:
                radar.send_json({'type':'control','action':'set_strip_autofill','params':{'on':False}})
                for viewer in (radar,viz):
                    receive_until(viewer,lambda m:m['type']=='session' and m['settings']['strip_autofill'] is False)
                assert client.app.state.session.world.strip_autofill is False
                radar.send_json({'type':'control','action':'load_scenario','params':{'scenario':'demo'}})
                receive_until(radar,lambda m:m['type']=='session' and m['settings']['strip_autofill'] is False)
                assert client.app.state.session.world.strip_autofill is False
                radar.send_json({'type':'control','action':'set_strip_autofill','params':{'on':'false'}})
                receive_until(radar,lambda m:m['type']=='caption' and 'boolean' in m['text'])
                assert client.app.state.session.world.strip_autofill is False
