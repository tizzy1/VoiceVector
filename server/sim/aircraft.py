"""Full server-side aircraft state (SIM_SPEC §1.1)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from server.contracts import Flow, LatMode, Phase, TurnDir, VertMode, Wake


class AircraftState(BaseModel):
    callsign: str
    spoken: str                                 # "speedbird one one seven"
    spoken_plain: str                           # "British Airways 117"
    actype: str
    wake: Wake
    persona: str
    flow: Flow
    phase: Phase
    # kinematics
    x: float
    y: float
    alt: float
    vs: float = 0.0
    hdg: float
    track: float
    ias: float
    tas: float
    gs: float
    # autopilot targets
    lat_mode: LatMode = "HDG"
    vert_mode: VertMode = "ALT"
    tgt_hdg: Optional[float] = None
    turn_dir: Optional[TurnDir] = None
    route: list[str] = Field(default_factory=list)
    route_idx: int = 0
    route_name: Optional[str] = None
    cleared_alt: float
    vs_constraint: Optional[tuple[str, int]] = None
    tgt_ias: Optional[float] = None
    spd_constraint: Optional[str] = None
    no_speed_restriction: bool = False
    # clearances & expectations
    approach_cleared: Optional[dict[str, Any]] = None
    loc_armed: bool = False
    gs_armed: bool = False
    established: bool = False
    expect: dict[str, Any] = Field(default_factory=dict)
    conditionals: list[dict[str, Any]] = Field(default_factory=list)
    squawk: str = "2000"
    qnh: int = 1013
    freq_unit: str = "director"
    on_frequency: bool = True
    in_jurisdiction: bool = True
    emergency: bool = False
    transmitting: bool = False
    # internal guidance state (not part of the spec's public model)
    auto_ias: Optional[float] = None            # speed the aircraft manages itself (hold, final)
    gp_alt: Optional[float] = None              # glidepath altitude target while vert_mode == GS
    expedite: bool = False
    hold: Optional[dict[str, Any]] = None       # {"fix", "inbound", "leg", "t", "prev_d"}
    hold_fix: Optional[str] = None              # instructed to hold at this fix on arrival
    report_established: bool = False
    nfc_called: bool = False                    # "no further clearance" already transmitted
    spawn_t: float = 0.0
    landed_t: Optional[float] = None
    checkin_t: Optional[float] = None           # arrival check-in time (expected-approach call)
    asked_expect: bool = False
    radio_failed: bool = False
    overshoot_called: bool = False
    loc_prev_xte: Optional[float] = None
    # departures (sim/runway.py)
    release_not_before: float = 0.0
    released_t: Optional[float] = None
    sid_started: bool = False
    dep_checked_in: bool = False
    levelled_t: Optional[float] = None
    higher_requested: bool = False
    # flight plan (SIM_SPEC §1.8, sim/flightplan.py)
    flight_rules: str = "I"
    adep: str = ""
    ades: str = ""
    rfl: int = 0                                # requested cruise level, FL number
    assigned_squawk: str = ""                   # ASSR
    strip: dict[str, Any] = Field(default_factory=dict)   # controller's list record (§5.5)
