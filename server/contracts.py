"""SweatboxAI shared contracts — SOURCE OF TRUTH for every module boundary.

Rules:
  * Do not change anything in this file without telling the whole team (group chat) and
    updating web/src/protocol.ts in the same commit. File owner / merger: Bhavin.
  * Adding an OPTIONAL field with a default is allowed (tell the team). Renaming/removing is not.
  * Units: distances NM, altitudes ft, speeds kt (IAS unless named otherwise), vertical speed ft/min,
    headings degrees TRUE 0-359.99, times = sim seconds (float) since session start unless suffixed _wall.
  * Positions: x = NM east, y = NM north of the RWY 26L landing threshold (data/egkk.json _meta.origin).
"""
from __future__ import annotations

from typing import Any, Callable, Literal, Optional, Protocol

from pydantic import BaseModel, Field

# =====================================================================================
# Enumerations
# =====================================================================================
Wake = Literal["H", "M", "L"]
Flow = Literal["ARR", "DEP"]
Phase = Literal["QUEUED", "TAKEOFF", "SID", "STAR", "VECTORS", "APPROACH", "FINAL",
                "GO_AROUND", "HOLD", "LANDED", "HANDED_OFF"]
LatMode = Literal["HDG", "LNAV", "LOC", "HOLD", "RWY_TRACK"]
VertMode = Literal["ALT", "LVL_CHG", "GS"]
TurnDir = Literal["L", "R"]
Role = Literal["radar", "viz"]
Speaker = str  # "CTRL" or a callsign such as "BAW117"

InstrType = Literal[
    # lateral
    "heading", "turn_by", "present_heading", "direct", "resume_nav", "hold",
    # vertical
    "altitude", "flight_level", "maintain_level", "vertical_rate", "expedite",
    # speed
    "speed", "speed_min_clean", "speed_cancel",
    # approach
    "approach_clearance", "intercept_localiser", "descend_glidepath", "cancel_approach", "go_around",
    # comms / admin
    "contact", "squawk", "qnh", "expect", "report", "confirm", "traffic_info",
    # recognised but not executed by Director (wrong position)
    "cleared_to_land", "cleared_takeoff",
]

Intent = Literal["instruction", "readback_correction", "say_again_request", "readback_request",
                 "confirm_request", "broadcast", "unknown"]

EvalStatus = Literal["OK", "UNABLE", "QUERY"]

ReasonCode = Literal[
    "UNABLE_SPEED_LOW", "UNABLE_SPEED_HIGH", "UNABLE_CEILING", "UNABLE_RATE",
    "UNABLE_INTERCEPT_ANGLE", "UNABLE_TOO_HIGH_GS", "UNABLE_NOT_MODELLED",
    "QUERY_LEVEL_BELOW_MIN", "QUERY_AMBIGUOUS", "QUERY_UNKNOWN_FIX", "QUERY_UNKNOWN_RUNWAY",
    "QUERY_WRONG_POSITION", "QUERY_CALLSIGN", "QUERY_LEVEL_TYPE", "QUERY_FIX_PASSED",
]

ReplyKind = Literal[
    "full",                 # full readback of a clearance
    "partial_correction",   # read back ONLY corrected elements (after "negative"/"I say again")
    "unable", "query",
    "report",               # answer to report/confirm ("cleared five thousand")
    "looking",              # traffic info response
    "say_again_request",    # "station calling Gatwick, say again your callsign"
    "repeat_last",          # pilot repeats its last transmission
    "checkin", "request", "no_further_clearance", "established",
    "going_around", "mayday", "pan", "recall",  # recall = re-call after being blocked
    "silence",
]

ErrorMode = Literal["consistent", "readback_only", "execution_only"]
ErrorKind = Literal[
    # readback
    "wrong_value_adjacent", "digit_transposition", "level_type_confusion", "turn_direction_swap",
    "runway_side_swap", "omitted_element", "wrong_callsign", "similar_callsign_takeover",
    # execution
    "wrong_turn_direction", "wrong_value_flown", "level_bust_overshoot", "delayed_compliance",
    "ignores_instruction",
]
ErrorOutcome = Literal["pending", "caught", "missed", "self_resolved"]
ErrorRate = Literal["off", "low", "med", "high"]

InjectKind = Literal["mayday", "pan", "go_around", "radio_failure", "spawn_conflict",
                     "force_overlap", "spawn_arrival", "spawn_departure"]

AlertKind = Literal["STCA_LOS", "STCA_PRED", "WAKE", "MIN_ALT", "OUTSIDE_CAS", "EMERGENCY"]
Severity = Literal["HIGH", "MEDIUM", "LOW", "POSITIVE", "INFO"]
Actor = Literal["CONTROLLER", "PILOT", "SYSTEM", "INSTRUCTOR"]

EventType = Literal[
    # comms
    "ctrl_ptt_down", "ctrl_ptt_up", "ctrl_transmission", "ctrl_blocked",
    "pilot_transmission", "pilot_blocked", "overlap",
    # sim
    "spawn", "takeoff", "landing", "handoff", "go_around", "hold_entered", "loc_captured",
    "gs_captured", "instruction_applied", "level_reached", "inject", "pilot_call", "strip_edit",
    # errors
    "error_planned", "error_spoken", "error_caught", "error_missed",
    # safety / rules
    "alert_start", "alert_end", "finding",
    # session
    "session_start", "session_pause", "session_resume", "session_end", "settings_changed",
]

# =====================================================================================
# Parsing (owner: Sanju produces, Tudor consumes)
# =====================================================================================
class Condition(BaseModel):
    after_fix: Optional[str] = None
    passing_level: Optional[float] = None  # ft


class Instruction(BaseModel):
    type: InstrType
    value: Optional[float | str] = None        # heading deg / altitude ft / FL as int (e.g. 70) / speed kt / squawk / qnh
    turn_dir: Optional[TurnDir] = None
    verb: Optional[Literal["climb", "descend", "maintain"]] = None
    constraint: Optional[Literal["exact", "or_greater", "or_less", "expedite", "when_ready",
                                 "max", "min"]] = None
    approach_type: Optional[Literal["ILS", "LOC", "RNP", "VISUAL"]] = None
    runway: Optional[str] = None               # "26L"
    fix: Optional[str] = None                  # "WILLO"
    unit: Optional[Literal["tower", "london", "director"]] = None
    frequency: Optional[str] = None            # "124.225"
    condition: Optional[Condition] = None
    expect_what: Optional[str] = None          # "ILS 26L" | "FL70" | "vectors"
    report_what: Optional[Literal["established", "heading", "speed", "level", "passing"]] = None
    confidence: float = 1.0
    span: Optional[tuple[int, int]] = None     # char span in Transmission.norm_text (caption highlight)


class Transmission(BaseModel):
    tx_id: str
    t: float                                   # sim time at PTT up
    receiver: Optional[str] = None             # resolved ICAO callsign, e.g. "BAW117"
    receiver_confidence: float = 0.0
    intent: Intent = "instruction"
    instructions: list[Instruction] = Field(default_factory=list)
    self_corrections: int = 0
    raw_text: str = ""
    norm_text: str = ""
    source: Literal["voice", "typed"] = "voice"
    ambiguities: list[str] = Field(default_factory=list)  # "callsign_low_conf", "similar_callsign", "unparsed_clause"...
    parse_path: Literal["regex", "llm", "mixed"] = "regex"
    timings: dict[str, float] = Field(default_factory=dict)  # wall-clock stage stamps, see VOICE_PIPELINE §4


class CallsignInfo(BaseModel):
    icao: str                                  # "BAW117"
    telephony: str                             # "Speedbird"
    spoken: str                                # "speedbird one one seven" (normalised form, digits as words)
    spoken_digits: str                         # "speedbird 117"
    on_frequency: bool = True

# =====================================================================================
# Simulation (owner: Tudor)
# =====================================================================================
class Alert(BaseModel):
    kind: AlertKind
    callsigns: list[str]
    severity: Severity
    message: str                               # plain English, e.g. "BAW117 and EZY45AB 2.4 NM, 600 ft"
    since: float


class AircraftPublic(BaseModel):
    """What clients render. Server computes BOTH local xy and lat/lon."""
    callsign: str
    spoken_plain: str                          # "British Airways 117" (for viz labels)
    actype: str
    wake: Wake
    flow: Flow
    phase: Phase
    x: float; y: float
    lat: float; lon: float
    alt: float; vs: float
    hdg: float; track: float
    ias: float; gs: float
    cleared_alt: float
    tgt_hdg: Optional[float] = None
    turn_dir: Optional[TurnDir] = None
    tgt_ias: Optional[float] = None
    route_name: Optional[str] = None
    next_fix: Optional[str] = None
    approach_cleared: Optional[str] = None     # "ILS 26L"
    established: bool = False
    squawk: str = "2000"
    on_frequency: bool = True
    in_jurisdiction: bool = True
    emergency: bool = False
    transmitting: bool = False                 # set by voice layer via hub (pilot audio playing)
    plain_status: str = ""                     # "descending to 4,000 ft · 210 kt" (viz labels)
    traffic_source: Optional[str] = None       # imported targets are SIMULATED after the snapshot
    source_time: Optional[str] = None
    fetched_at: Optional[str] = None
    source_attribution: Optional[str] = None
    # Flight-plan/list additions (CONTRACTS §3.2). None means not supplied by the simulator.
    flight_rules: Optional[Literal["I", "V"]] = None
    adep: Optional[str] = None
    ades: Optional[str] = None
    rfl: Optional[int] = None                  # requested cruise FL, e.g. 350 (not feet)
    assigned_squawk: Optional[str] = None      # four octal digits; preserve leading zeroes
    selected_level: Optional[float] = None    # Mode S selected level, feet (not strip CFL)
    distance_to_touchdown_nm: Optional[float] = None  # simulator track distance, never UI estimate
    strip: dict[str, Any] = Field(default_factory=dict)  # controller record, never a clearance


class CloudLayer(BaseModel):
    cover: str                                 # FEW | SCT | BKN | OVC
    base_ft: int


class Metar(BaseModel):
    """Session weather for the scope's METAR panel. Always SIMULATED: built from the sim's
    own wind and QNH, never a live report. Label it as simulated on screen."""
    source: Literal["SIMULATED"] = "SIMULATED"
    icao: str
    observed: str                              # "17:50Z"
    wind_dir_deg: Optional[int] = None         # None when calm
    wind_kt: int
    calm: bool
    visibility_m: int
    cloud: list[CloudLayer] = Field(default_factory=list)
    cavok: bool
    temp_c: int
    dewpoint_c: int
    qnh_hpa: int
    raw: str                                   # "EGKK 141750Z 00000KT 9999 SCT035 11/06 Q1013"


class LiveImportedTarget(BaseModel):
    callsign: str
    flow: Flow
    phase: Phase
    actype: str
    type_exact: bool


class LiveImportSkip(BaseModel):
    callsign: str
    reason: str


class LiveImportCounts(BaseModel):
    imported: int = 0
    skipped: int = 0
    skipped_by_reason: dict[str, int] = Field(default_factory=dict)


class LiveImportStatus(BaseModel):
    source: Literal["demo", "vatsim"]
    mode: Literal["snapshot"] = "snapshot"
    phase: Literal["fetching", "complete"] = "complete"
    ok: bool = False
    stale: bool = False
    error: Optional[str] = None
    fetched_at: Optional[str] = None
    source_time: Optional[str] = None
    attribution: str = ""
    sim_t: float = 0
    imported: list[LiveImportedTarget] = Field(default_factory=list)
    skipped: list[LiveImportSkip] = Field(default_factory=list)  # bounded detail sample, counts are complete
    counts: LiveImportCounts = Field(default_factory=LiveImportCounts)


class StateMsg(BaseModel):
    type: Literal["state"] = "state"
    t: float
    paused: bool
    sim_speed: float
    runway: str
    aircraft: list[AircraftPublic]
    alerts: list[Alert]
    backlog: dict[str, int] = Field(default_factory=dict)  # {"ARR": 2, "DEP": 1}
    metar: Optional[Metar] = None              # optional, additive (CONTRACTS §7.1)
    live_import_available: bool = False
    live_import: Optional[LiveImportStatus] = None


class WorldSnapshot(BaseModel):
    """Frozen full state for evaluation/rules (server-internal; may hold more than AircraftPublic)."""
    t: float
    aircraft: dict[str, dict[str, Any]]        # callsign -> full AircraftState as dict (SIM_SPEC §1.1)
    alerts: list[Alert]


class Evaluation(BaseModel):
    instr_idx: int                             # index into Transmission.instructions
    status: EvalStatus
    reason_code: Optional[ReasonCode] = None
    reason_values: dict[str, Any] = Field(default_factory=dict)  # e.g. {"min_speed": 210}


class ClearanceElement(BaseModel):
    type: InstrType
    intended: Instruction                      # what the controller said
    readback: Instruction                      # what the pilot says (may differ: planned error)
    flown: Instruction                         # what the aircraft does (may differ)
    status: Literal["active", "superseded", "corrected"] = "active"
    error_id: Optional[str] = None
    delay_s: float = 0.0                       # extra delay before applying (delayed_compliance)

# =====================================================================================
# Pilot logic (owner: Tudor) -> rendered by voice (owner: Sanju)
# =====================================================================================
class ErrorRecord(BaseModel):
    id: str
    callsign: str
    kind: ErrorKind
    mode: ErrorMode
    element_type: InstrType
    intended: Any
    readback_value: Any
    flown_value: Any
    t_planned: float
    t_spoken: Optional[float] = None
    t_caught: Optional[float] = None
    outcome: ErrorOutcome = "pending"
    caught_by: Optional[Literal["readback_correction", "execution_correction", "confirm_request",
                                "readback_request"]] = None


class ReadbackPlan(BaseModel):
    """Everything the composer needs. Values already include planned errors. Composer must not change them."""
    callsign: str                              # whose voice + callsign spoken in the readback
    persona: str                               # "crisp_airline" | "calm_longhaul" | "student" | "rushed" | "stressed"
    kind: ReplyKind
    elements: list[Instruction] = Field(default_factory=list)  # read back these, in any order, all of them
    reason_code: Optional[ReasonCode] = None   # for unable/query
    reason_values: dict[str, Any] = Field(default_factory=dict)
    report_values: dict[str, Any] = Field(default_factory=dict)  # report/checkin/request content, e.g. {"level_fl": 90, "star": "WILLO1G", "atis": "K"}
    seed: int = 0


class PilotResponse(BaseModel):
    callsign: Optional[str]                    # None/kind=silence -> nobody replies
    kind: ReplyKind
    plan: Optional[ReadbackPlan] = None
    apply: list[ClearanceElement] = Field(default_factory=list)  # applied after readback ends
    apply_delay_s: float = 1.0                 # seconds after readback end
    reaction_delay_s: float = 1.0              # pilot think-time before speaking (persona)
    errors: list[ErrorRecord] = Field(default_factory=list)
    in_reply_to: Optional[str] = None          # tx_id
    priority: int = 0                          # mayday > normal (reply scheduler)


class Readback(BaseModel):
    """Composer output (owner: Sanju)."""
    text: str                                  # display text ("Left heading 240, descend 4000, Speedbird 117")
    tts_text: str                              # speech text, numbers as words ("…two four zero…")
    variant_ids: list[str] = Field(default_factory=list)
    validated: bool = True
    fallback_used: bool = False

# =====================================================================================
# Events, findings, report
# =====================================================================================
class Event(BaseModel):
    t: float
    type: EventType
    actor: Actor
    callsign: Optional[str] = None
    tx_id: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    id: str
    rule_id: str                               # "A1", "B4", "D2"...
    category: Literal["A_SAFETY", "B_INSTRUCTION", "C_PHRASEOLOGY", "D_READBACK",
                      "E_COMPLIANCE", "F_EFFICIENCY"]
    severity: Severity
    t: float
    actor: Actor
    callsigns: list[str] = Field(default_factory=list)
    title: str                                 # "Loss of separation: BAW117 / EZY45AB"
    detail: str                                # "Minimum 2.1 NM / 400 ft at 12:41"
    correct_practice: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)  # {"tx_id":..., "transcript":..., "state":...}


class Report(BaseModel):
    session_id: str
    duration_s: float
    score: int
    score_breakdown: dict[str, int]
    findings: list[Finding]
    errors: list[ErrorRecord]
    stats: dict[str, Any]                      # landings, departures, transmissions, latency p50/p90...
    timeline: list[Event]                      # filtered key events for the UI timeline
    narrative: Optional[str] = None            # filled by Sanju's narrative writer
    coaching: list[str] = Field(default_factory=list)

# =====================================================================================
# Session / control / WS messages (owner: Bhavin)
# =====================================================================================
class SessionSettings(BaseModel):
    scenario: Literal["demo", "ramp", "easy", "medium", "busy", "custom", "judge"] = "demo"   # judge: added 15 Nov (Sanju)
    seed: int = 42
    runway: Literal["26L", "08R"] = "26L"
    arr_per_hr: float = 12
    dep_per_hr: float = 8
    error_rate: ErrorRate = "low"
    sim_speed: float = 1.0
    paused: bool = False
    strip_autofill: bool = True


class CaptionWord(BaseModel):
    w: str
    t0: float                                  # seconds from audio start
    t1: float


class ParsedChip(BaseModel):
    label: str                                 # "HDG 240 L", "ALT 4000", "ILS 26L", "BAW117"
    span: Optional[tuple[int, int]] = None


class CaptionMsg(BaseModel):
    type: Literal["caption"] = "caption"
    tx_id: str
    speaker: Speaker
    stage: Literal["partial", "final", "pilot_text", "pilot_words"]
    text: str
    chips: list[ParsedChip] = Field(default_factory=list)      # final controller captions
    words: list[CaptionWord] = Field(default_factory=list)     # pilot_words
    plain_english: str = ""                                    # "BA117 turning left to 240°, descending to 4,000 ft"
    latency: dict[str, float] = Field(default_factory=dict)    # {"parse_s":0.21,"first_audio_s":0.62}
    error_reveal: Optional[str] = None                         # set ONLY when demo "reveal errors" is on


TraceCategory = Literal["capture", "stt", "language", "sim", "pilot", "voice", "playback"]


class TraceStage(BaseModel):
    name: str                                  # VOICE_PIPELINE §20.1; includes blocked on failure
    category: TraceCategory
    t0_ms: float                               # relative to PTT up or pilot compose start
    t1_ms: float                               # speech may start before the anchor
    status: Literal["ok", "fallback", "error", "skipped"] = "ok"
    detail: dict[str, Any] = Field(default_factory=dict)


class PipelineTrace(BaseModel):
    tx_id: str
    speaker: Speaker
    reply_callsign: Optional[str] = None
    stages: list[TraceStage] = Field(default_factory=list)
    total_ms: float = 0.0                       # anchor to server playback release, milliseconds
    final: bool = False                        # provisional updates until audio ends


class CoachNote(BaseModel):
    level: Literal["good", "tip", "fix"]
    text: str


class CoachMsg(BaseModel):
    """Phraseology feedback on one controller transmission (added 15 Nov by Sanju; additive).
    Sent once from the rules (``tip`` empty), then again with the same tx_id when Claude's tip arrives."""
    type: Literal["coach"] = "coach"
    tx_id: str
    callsign: Optional[str] = None
    score: int = 100                           # 0-100
    notes: list[CoachNote] = Field(default_factory=list)
    tip: Optional[str] = None                  # one sentence from Claude Haiku, when available


class TraceMsg(BaseModel):
    type: Literal["trace"] = "trace"
    trace: PipelineTrace


class AudioStartMsg(BaseModel):
    type: Literal["audio_start"] = "audio_start"
    tx_id: str
    callsign: str
    format: Literal["pcm_s16le", "mp3"] = "pcm_s16le"
    sample_rate: int = 22050
    blocked: bool = False                      # play heterodyned (stepped-on)
    overlap_group: Optional[str] = None        # tx_ids sharing a group overlap
    emotion: Literal["normal", "stressed"] = "normal"


class AudioChunkMsg(BaseModel):
    type: Literal["audio_chunk"] = "audio_chunk"
    tx_id: str
    seq: int
    data_b64: str


class AudioEndMsg(BaseModel):
    type: Literal["audio_end"] = "audio_end"
    tx_id: str


class EventMsg(BaseModel):
    type: Literal["event"] = "event"
    event: Event
    feed_text: str = ""                        # human line for the event feed ("Readback error caught ✓ BAW117")


class ReportMsg(BaseModel):
    type: Literal["report"] = "report"
    report: Report


class SessionMsg(BaseModel):
    type: Literal["session"] = "session"
    code: str
    settings: SessionSettings
    clients: dict[str, int]                    # {"radar":1,"viz":1}
    join_url: str


ControlAction = Literal["new_session", "pause", "resume", "set_speed", "set_traffic", "set_error_rate",
                        "set_runway", "load_scenario", "inject", "force_error", "beat", "end_session",
                        "reveal_errors", "import_live", "edit_strip", "set_strip_autofill"]


class ControlMsg(BaseModel):                   # client -> server
    type: Literal["control"] = "control"
    action: ControlAction
    params: dict[str, Any] = Field(default_factory=dict)


class PttMsg(BaseModel):                       # client (radar) -> server
    type: Literal["ptt"] = "ptt"
    state: Literal["down", "up"]


class TypedMsg(BaseModel):                     # client (radar) -> server
    type: Literal["typed"] = "typed"
    text: str
# Binary WS frames from radar client = microphone PCM16 mono 16 kHz, little-endian, ~100 ms per frame.

# =====================================================================================
# Module interfaces (implement EXACTLY these signatures)
# =====================================================================================
class EventBus(Protocol):                      # owner: Bhavin (server/session.py)
    def emit(self, e: Event) -> None: ...
    def subscribe(self, cb: Callable[[Event], None]) -> None: ...
    def now(self) -> float: ...                # current sim time


class Broadcaster(Protocol):                   # owner: Bhavin (server/hub.py)
    async def send(self, msg: BaseModel | dict, roles: Optional[set[Role]] = None) -> None: ...


class WorldAPI(Protocol):                      # owner: Tudor (server/sim/world.py: class World)
    t: float                                   # current sim time (s); EventBus.now() reads this
    def configure(self, settings: SessionSettings) -> None: ...
    def load_scenario(self, name: str, seed: int) -> None: ...
    def tick(self, dt: float) -> None: ...     # dt in sim seconds; emits events on the bus
    def public_state(self) -> StateMsg: ...
    def snapshot(self) -> WorldSnapshot: ...
    def callsigns(self) -> list[CallsignInfo]: ...
    def fixes(self) -> list[str]: ...
    def edit_strip(self, callsign: str, field: str, value: Any) -> bool: ...
    def evaluate(self, tx: Transmission) -> list[Evaluation]: ...
    def schedule_apply(self, callsign: str, elements: list[ClearanceElement], at: float) -> None: ...
    def set_transmitting(self, callsign: str, on: bool) -> None: ...
    def inject(self, kind: InjectKind, callsign: Optional[str] = None,
               params: Optional[dict[str, Any]] = None) -> None: ...


class PilotAPI(Protocol):                      # owner: Tudor (server/pilot/engine.py: class PilotEngine)
    def handle_transmission(self, tx: Transmission, evaluations: list[Evaluation]) -> list[PilotResponse]: ...
    def on_sim_event(self, e: Event) -> Optional[PilotResponse]: ...
    def on_readback_spoken(self, resp: PilotResponse, t_end: float) -> None: ...
    def tick(self, t: float) -> None: ...      # called every sim tick: error timers (missed), re-call timers
    def on_blocked(self, callsign: str, tx_id: str) -> Optional[PilotResponse]: ...  # schedule re-call
    def set_error_rate(self, rate: ErrorRate) -> None: ...
    def force_error(self, mode: ErrorMode, kind: Optional[ErrorKind] = None,
                    callsign: Optional[str] = None) -> None: ...


class ComposerAPI(Protocol):                   # owner: Sanju (server/pilot/readback.py)
    def compose(self, plan: ReadbackPlan) -> Readback: ...  # validated; never alters plan values


class VoiceAPI(Protocol):                      # owner: Sanju (server/voice/pipeline.py: class VoicePipeline)
    async def on_ptt(self, state: Literal["down", "up"]) -> None: ...
    async def on_audio_in(self, pcm16: bytes) -> None: ...
    async def on_typed(self, text: str) -> None: ...
    async def speak(self, resp: PilotResponse) -> None: ...   # compose, TTS, schedule, stream, then apply
    def is_frequency_busy(self) -> bool: ...
    def set_reveal_errors(self, on: bool) -> None: ...        # demo toggle: add error_reveal to captions


class RulesAPI(Protocol):                      # owner: Tudor (server/rules/engine.py: class RulesEngine)
    def on_event(self, e: Event) -> None: ...
    def on_tick(self, snap: WorldSnapshot) -> None: ...
    def report(self, session_id: str) -> Report: ...


class NarrativeAPI(Protocol):                  # owner: Sanju (server/agents/narrative.py)
    async def write(self, report: Report) -> Report: ...      # fills narrative + coaching only
