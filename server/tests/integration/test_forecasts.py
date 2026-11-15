from fastapi.testclient import TestClient
from server.contracts import ConflictForecast, ForecastPath, Instruction, ClearanceElement, StateMsg
from server.main import create_app
from server.tests.integration.test_session_controls import FACTORIES, receive_until


def test_conflict_beat_publishes_real_advisory_values_to_both_viewers(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect('/ws?role=radar') as radar:
            with client.websocket_connect('/ws?role=viz') as viz:
                radar.send_json({'type':'control','action':'beat','params':{'key':2}})
                states=[receive_until(v,lambda m:m['type']=='state' and any(f['p_conflict']>=.15 for f in m['forecasts'])) for v in (radar,viz)]
                assert states[0]['forecasts']==states[1]['forecasts']
                for state in states:
                    f=state['forecasts'][0]
                    assert f['rollouts']==64 and f['horizon_s']==120
                    assert state['t']-f['computed_t']<=2
                    assert len(f['samples']) == 16
                    assert all(len(s['points']) == 9 for s in f['samples'])
                    assert {s['callsign'] for s in f['samples']} == set(f['pair'])


def test_adapter_preserves_flights_and_exports_actual_forecast_positions(tmp_path):
    with TestClient(create_app(FACTORIES,tmp_path)) as client:
        session=client.app.state.session
        def check():
            session.world.inject('spawn_conflict')
            session.world.tick(1)
            before=session.world.snapshot().model_dump()
            state=session.state()
            assert state.forecasts_available and state.forecasts
            raw = session.world.forecasts[0]
            exported = state.forecasts[0]
            assert len(exported.samples) == 16
            for sample in exported.samples:
                xy = raw['paths'][sample.callsign]
                path_index = [s.callsign for s in exported.samples].index(sample.callsign)
                local_index = exported.samples.index(sample) - path_index
                for point, (x, y) in zip(sample.points, xy[local_index]):
                    px, py = session.world.proj.to_xy(point[0], point[1])
                    assert abs(px-x) <= .006 and abs(py-y) <= .006
                    assert 0 < point[2] < 60000
            assert exported.samples[0].points == [tuple(p) for p in raw['samples'][0]['points']]
            assert session.world.snapshot().model_dump()==before
            return state
        state=client.portal.call(check)
        assert StateMsg.model_validate_json(state.model_dump_json()).forecasts==state.forecasts


def test_typed_forecast_samples_pass_through_with_correct_units(tmp_path):
    with TestClient(create_app(FACTORIES,tmp_path)) as client:
        session=client.app.state.session
        base=session.world.public_state()
        forecast=ConflictForecast(pair=('A','B'),p_conflict=.625,t_conflict_s=80,
            min_sep_nm=1.8,min_sep_ft=300,computed_t=base.t,
            samples=[ForecastPath(callsign='A',points=[(51.1,-.2,7000),(51.11,-.19,6000)])])
        state=base.model_copy(update={'forecasts':[forecast],'forecasts_available':True,'forecasts_computed_t':base.t})
        session.world.public_state=lambda:state
        assert session.state().forecasts[0].samples[0].points[1]==(51.11,-.19,6000)


def test_resolving_pair_reduces_forecast_without_using_ui_estimates(tmp_path):
    with TestClient(create_app(FACTORIES,tmp_path)) as client:
        session=client.app.state.session
        def check():
            w=session.world
            w.inject('spawn_conflict')
            w.tick(1)
            f=session.state().forecasts[0]
            target=f.pair[0]
            ins=Instruction(type='flight_level',value=110)
            w.schedule_apply(target,[ClearanceElement(type=ins.type,intended=ins,readback=ins,flown=ins)],w.t)
            for _ in range(20):w.tick(1)
            final=next((r.p_conflict for r in session.state().forecasts if set(r.pair)==set(f.pair)),0)
            assert final < f.p_conflict
        client.portal.call(check)
