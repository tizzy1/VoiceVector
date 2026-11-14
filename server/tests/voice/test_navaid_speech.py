"""Pilots say navaids by station name: SFD = "Seaford", SFD1X = "Seaford one X-ray"."""
from pathlib import Path

from server.contracts import Instruction, ReadbackPlan
from server.pilot.readback import Composer

DATA = Path(__file__).resolve().parents[3] / "data"


def compose(kind, **kw):
    return Composer(DATA).compose(ReadbackPlan(callsign="BAW117", persona="crisp_airline",
                                               kind=kind, **kw))


def test_direct_to_a_vor_uses_its_name():
    rb = compose("full", elements=[Instruction(type="direct", fix="SFD")])
    assert "seaford" in rb.tts_text and "sfd" not in rb.tts_text.split()
    assert "Seaford" in rb.text and rb.validated and not rb.fallback_used


def test_five_letter_fix_is_said_as_written():
    rb = compose("full", elements=[Instruction(type="direct", fix="WILLO")])
    assert "willo" in rb.tts_text and rb.validated


def test_procedure_names_use_the_station_name():
    sid = compose("checkin", report_values={"alt": 2000, "passing": True,
                                            "cleared_alt": 6000, "route": "SFD1X"})
    assert "seaford one" in sid.tts_text
    star = compose("checkin", report_values={"level_fl": 110, "route": "MID1X", "atis": "K"})
    assert "midhurst one" in star.tts_text
    plain = compose("checkin", report_values={"level_fl": 120, "route": "WILLO1G"})
    assert "willo one golf" in plain.tts_text


def test_every_navaid_in_the_airspace_data_has_a_name():
    import json
    fixes = json.loads((DATA / "egkk.json").read_text())["fixes"]
    names = json.loads((DATA / "spoken_names.json").read_text())["navaids"]
    navaids = {f for f in fixes if len(f) == 3 and f.isalpha()}
    assert navaids <= set(names)
