"""PilotEngine — pilot decisions, errors and corrections (owner: Tudor).

VOICE_PIPELINE §8 (responder), §9 (decision), §10 (error policy), §14 (corrections,
confirm, read back, say again, missed timers). Deterministic: all randomness is seeded.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Optional

from server.contracts import (
    ClearanceElement, ErrorKind, ErrorMode, ErrorRate, ErrorRecord, Evaluation, Event, EventBus,
    Instruction, PilotResponse, ReadbackPlan, Transmission, WorldAPI,
)
from server.pilot import errors as policy
from server.pilot.clearance import ClearanceBook, same_content
from server.pilot.decision import LOW_CONFIDENCE, first_refusal, is_report_only, report_values

REACTION_DELAY_S: dict[str, tuple[float, float]] = {
    "crisp_airline": (0.8, 1.2),
    "calm_longhaul": (1.0, 1.5),
    "student": (1.5, 2.5),
    "rushed": (0.5, 0.9),
    "stressed": (0.4, 0.8),
}
DEFAULT_PERSONA = "crisp_airline"
READBACK_MISS_S = 30.0
EXECUTION_MISS_S = 60.0
CONFIRM_WINDOW_S = 60.0
NOT_ERROR_ELIGIBLE = ("confirm", "report", "traffic_info", "expect", "contact", "go_around",
                      "cleared_to_land", "cleared_takeoff", "squawk", "qnh")


class PilotEngine:
    def __init__(self, world: WorldAPI, bus: EventBus, data_dir: Path) -> None:
        self.world = world
        self.bus = bus
        self.data_dir = Path(data_dir)
        self.error_rate: ErrorRate = "low"
        self._forced: Optional[tuple[ErrorMode, Optional[ErrorKind], Optional[str]]] = None
        seed = getattr(world, "seed", 42)
        self._rng = random.Random(seed + 2)          # delays
        self._err_rng = random.Random(seed + 3)      # error policy
        self._n = 0
        self._err_n = 0
        self.book = ClearanceBook()
        self.errors: dict[str, ErrorRecord] = {}
        self._deadline: dict[str, float] = {}       # error id -> miss time
        self._last: dict[str, PilotResponse] = {}   # cs -> last spoken response
        self._confirmed: dict[str, float] = {}      # cs -> t of last confirm request

    # ----------------------------------------------------------- helpers
    def _t(self) -> float:
        return float(getattr(self.world, "t", 0.0))

    def _state(self, cs: str) -> dict[str, Any]:
        ac = getattr(self.world, "aircraft", {}).get(cs)
        if ac is not None:
            return {"persona": ac.persona, "cleared_alt": ac.cleared_alt, "hdg": ac.hdg,
                    "ias": ac.ias, "established": ac.established, "emergency": ac.emergency}
        return self.world.snapshot().aircraft.get(cs, {})

    def _persona(self, cs: str) -> str:
        p = self._state(cs).get("persona", DEFAULT_PERSONA)
        return p if p in REACTION_DELAY_S else DEFAULT_PERSONA

    def _reaction(self, cs: str) -> float:
        return round(self._rng.uniform(*REACTION_DELAY_S[self._persona(cs)]), 2)

    def _plan(self, cs: str, kind: str, **kw) -> ReadbackPlan:
        self._n += 1
        return ReadbackPlan(callsign=cs, persona=self._persona(cs), kind=kind,
                            seed=getattr(self.world, "seed", 42) * 1000 + self._n, **kw)

    def _resp(self, cs: str, kind: str, tx: Optional[Transmission] = None,
              apply: Optional[list[ClearanceElement]] = None,
              errors: Optional[list[ErrorRecord]] = None, **plan_kw) -> PilotResponse:
        return PilotResponse(
            callsign=cs, kind=kind, plan=self._plan(cs, kind, **plan_kw), apply=apply or [],
            errors=errors or [], in_reply_to=tx.tx_id if tx else None,
            reaction_delay_s=self._reaction(cs),
            apply_delay_s=round(self._rng.uniform(0.5, 2.0), 2) if apply else 1.0)

    def _emit(self, type_: str, cs: Optional[str], data: dict[str, Any],
              tx_id: Optional[str] = None) -> None:
        self.bus.emit(Event(t=self._t(), type=type_, actor="PILOT", callsign=cs, tx_id=tx_id,
                            data=data))

    def _open_errors(self, cs: Optional[str] = None) -> list[ErrorRecord]:
        return [e for e in self.errors.values() if e.outcome == "pending"
                and (cs is None or e.callsign == cs)]

    def _on_freq(self) -> set[str]:
        return {c.icao for c in self.world.callsigns()}

    # ------------------------------------------------------------- API
    def handle_transmission(self, tx: Transmission,
                            evaluations: list[Evaluation]) -> list[PilotResponse]:
        on_freq = self._on_freq()
        cs = tx.receiver
        if cs is None or cs not in on_freq:
            if cs is None and tx.instructions and on_freq and self._rng.random() < 0.5:
                who = sorted(on_freq)[0]
                return [self._resp(who, "say_again_request", tx)]
            return []                                          # silence
        if tx.source == "voice" and 0 < tx.receiver_confidence < LOW_CONFIDENCE:
            return [self._resp(cs, "query", tx, reason_code="QUERY_CALLSIGN",
                               reason_values={"callsign": cs})]

        if tx.intent == "say_again_request":
            return self._repeat_last(cs, tx)
        if tx.intent == "readback_request":
            return self._full_reread(cs, tx)
        if tx.intent == "confirm_request" or is_report_only(tx.instructions):
            return self._report(cs, tx)
        if not tx.instructions:
            return []
        if tx.intent == "readback_correction":
            return self._correction(cs, tx, evaluations)

        reread = self._catch_omissions(cs, tx)
        if reread and len(reread) == len(tx.instructions):
            return [self._resp(cs, "partial_correction", tx, elements=reread)]
        self._miss_open(cs, reason="next_transmission")
        refused = first_refusal(evaluations)
        if refused is not None:
            kind = "unable" if refused.status == "UNABLE" else "query"
            return [self._resp(cs, kind, tx, reason_code=refused.reason_code,
                               reason_values=refused.reason_values)]
        if all(i.type == "traffic_info" for i in tx.instructions):
            return [self._resp(cs, "looking", tx)]
        return [self._full(cs, tx)]

    # --------------------------------------------------- full clearance
    def _full(self, cs: str, tx: Transmission) -> PilotResponse:
        elements = [ClearanceElement(type=i.type, intended=i.model_copy(),
                                     readback=i.model_copy(), flown=i.model_copy())
                    for i in tx.instructions]
        extra: list[ClearanceElement] = []
        records: list[ErrorRecord] = []
        planned = self._plan_error(cs, elements)
        if planned is not None:
            idx, new, extra, rec = planned
            elements[idx] = new
            records.append(rec)
        self.book.record(cs, [e for e in elements if e.type not in NOT_ERROR_ELIGIBLE
                              or e.type in ("contact", "squawk", "qnh")])
        spoken = [e.readback for e in elements if not self._omitted(e)]
        return self._resp(cs, "full", tx, apply=elements + extra, errors=records,
                          elements=spoken)

    def _omitted(self, el: ClearanceElement) -> bool:
        rec = self.errors.get(el.error_id or "")
        return rec is not None and rec.kind == "omitted_element"

    def _catch_omissions(self, cs: str, tx: Transmission) -> list[Instruction]:
        """Controller repeated an element the pilot left out of the readback -> caught."""
        heard: list[Instruction] = []
        for ins in tx.instructions:
            el = self.book.match(cs, ins)
            rec = self.errors.get(el.error_id or "") if el is not None else None
            if (rec is not None and rec.kind == "omitted_element" and rec.outcome == "pending"
                    and same_content(ins, el.intended)):
                self._catch(rec, tx)
                heard.append(ins.model_copy())
        return heard

    def _plan_error(self, cs: str, elements: list[ClearanceElement]):
        forced = self._forced
        use_forced = forced is not None and forced[2] in (None, cs)
        if self._state(cs).get("emergency"):
            return None
        if not use_forced:
            if len(self._open_errors(cs)) >= policy.MAX_PER_AIRCRAFT:
                return None
            if len(self._open_errors()) >= policy.MAX_GLOBAL:
                return None
            if self._err_rng.random() >= policy.RATE.get(self.error_rate, 0.0):
                return None
            mode = self._weighted_mode()
            kind = None
        else:
            mode, kind, _ = forced
        eligible = [i for i, e in enumerate(elements) if e.type not in NOT_ERROR_ELIGIBLE]
        self._err_rng.shuffle(eligible)
        kinds = policy.candidates(mode, kind)
        if len(elements) < 2:                       # omitting the only element = no readback
            kinds = [k for k in kinds if k != "omitted_element"]
        for idx in eligible:
            for k in self._err_rng.sample(kinds, len(kinds)):
                built = policy.build(k, mode, elements[idx], self._err_rng)
                if built is None:
                    continue
                new, extra, rb_val, fl_val = built
                good = elements
                bad = elements[:idx] + [new] + elements[idx + 1:] + extra
                if new.flown != elements[idx].flown or new.delay_s:
                    if policy.causes_los(self.world, cs, good, bad):
                        continue
                if use_forced:
                    self._forced = None
                self._err_n += 1
                rec = ErrorRecord(
                    id=f"E{self._err_n}", callsign=cs, kind=k, mode=mode,
                    element_type=new.type, intended=policy._val(new.intended),
                    readback_value=rb_val, flown_value=fl_val, t_planned=self._t())
                new.error_id = rec.id
                for x in extra:
                    x.error_id = rec.id
                self.errors[rec.id] = rec
                self._emit("error_planned", cs, rec.model_dump())
                return idx, new, extra, rec
        return None

    def _weighted_mode(self) -> ErrorMode:
        r, acc = self._err_rng.random(), 0.0
        for mode, w in policy.MODE_WEIGHTS:
            acc += w
            if r < acc:
                return mode
        return "consistent"

    # ------------------------------------------------------ corrections
    def _correction(self, cs: str, tx: Transmission,
                    evaluations: list[Evaluation]) -> list[PilotResponse]:
        refused = first_refusal(evaluations)
        if refused is not None:
            kind = "unable" if refused.status == "UNABLE" else "query"
            return [self._resp(cs, kind, tx, reason_code=refused.reason_code,
                               reason_values=refused.reason_values)]
        corrected: list[ClearanceElement] = []
        mentioned: list[Instruction] = []
        reread = self._catch_omissions(cs, tx)
        for ins in tx.instructions:
            if any(same_content(ins, h) for h in reread):
                continue
            el = self.book.match(cs, ins)
            if el is not None and same_content(ins, el.readback) and same_content(ins, el.flown):
                mentioned.append(ins)                       # pilot already had it right
                continue
            fixed = ClearanceElement(type=ins.type, intended=ins.model_copy(),
                                     readback=ins.model_copy(), flown=ins.model_copy(),
                                     status="corrected")
            if el is not None and el.error_id and el.error_id in self.errors:
                self._catch(self.errors[el.error_id], tx)
            self.book.record(cs, [fixed])
            corrected.append(fixed)
        if not corrected and reread:
            return [self._resp(cs, "partial_correction", tx, elements=reread)]
        if not corrected:
            self._emit("finding", cs, {
                "rule_id": "D5", "title": f"Unnecessary correction: {cs}",
                "detail": "The readback was already correct.", "tx_id": tx.tx_id}, tx.tx_id)
            return [self._resp(cs, "partial_correction", tx,
                               elements=[i.model_copy() for i in mentioned])]
        return [self._resp(cs, "partial_correction", tx, apply=corrected,
                           elements=[e.readback for e in corrected])]

    def _catch(self, rec: ErrorRecord, tx: Transmission) -> None:
        if rec.outcome != "pending":
            return
        if rec.mode == "execution_only":
            how = "execution_correction"
        elif self._t() - self._confirmed.get(rec.callsign, -1e9) <= CONFIRM_WINDOW_S:
            how = "confirm_request"
        else:
            how = "readback_correction"
        rec.outcome, rec.t_caught, rec.caught_by = "caught", self._t(), how
        self._deadline.pop(rec.id, None)
        self._emit("error_caught", rec.callsign, rec.model_dump(), tx.tx_id)

    def _miss_open(self, cs: str, reason: str) -> None:
        for rec in self._open_errors(cs):
            if rec.t_spoken is None and rec.mode != "execution_only":
                continue                                   # not heard yet: can't be missed
            self._miss(rec, reason)

    def _miss(self, rec: ErrorRecord, reason: str) -> None:
        rec.outcome = "missed"
        self._deadline.pop(rec.id, None)
        self._emit("error_missed", rec.callsign, {**rec.model_dump(), "reason": reason})

    # -------------------------------------------- confirm / say again / read back
    def _report(self, cs: str, tx: Transmission) -> list[PilotResponse]:
        what = next((i.report_what for i in tx.instructions if i.type in ("confirm", "report")),
                    None)
        if tx.intent == "confirm_request" or any(i.type == "confirm" for i in tx.instructions):
            self._confirmed[cs] = self._t()
        vals = report_values(what, self.book.believed(cs), self._state(cs))
        return [self._resp(cs, "report", tx, report_values=vals)]

    def _repeat_last(self, cs: str, tx: Transmission) -> list[PilotResponse]:
        last = self._last.get(cs)
        if last is None or last.plan is None:
            return [self._resp(cs, "say_again_request", tx)]
        plan = last.plan.model_copy(update={"kind": "repeat_last"})
        return [PilotResponse(callsign=cs, kind="repeat_last", plan=plan, in_reply_to=tx.tx_id,
                              reaction_delay_s=self._reaction(cs))]

    def _full_reread(self, cs: str, tx: Transmission) -> list[PilotResponse]:
        els = [e for e in self.book.last_full.get(cs, []) if e.status != "superseded"]
        if not els:
            return [self._resp(cs, "say_again_request", tx)]
        return [self._resp(cs, "full", tx, elements=[e.readback for e in els])]

    # ---------------------------------------------------- lifecycle hooks
    def on_sim_event(self, e: Event) -> Optional[PilotResponse]:
        if e.type in ("landing", "handoff") and e.callsign:
            self.book.forget(e.callsign) if e.type == "landing" else None
            return None
        if e.type != "pilot_call" or not e.callsign:
            return None
        kind = e.data.get("kind", "checkin")
        values = {k: v for k, v in e.data.items() if k not in ("kind", "reason_code")}
        extra = {"reason_code": e.data["reason_code"]} if e.data.get("reason_code") else {}
        plan = self._plan(e.callsign, kind, report_values=values, **extra)
        return PilotResponse(callsign=e.callsign, kind=kind, plan=plan,
                             reaction_delay_s=self._reaction(e.callsign),
                             priority=10 if kind in ("mayday", "pan") else 0)

    def on_readback_spoken(self, resp: PilotResponse, t_end: float) -> None:
        if resp.callsign and resp.kind != "repeat_last" and resp.plan is not None:
            self._last[resp.callsign] = resp
        for r in resp.errors:
            rec = self.errors.get(r.id)
            if rec is None or rec.outcome != "pending":
                continue
            if rec.mode == "execution_only":
                self._deadline[rec.id] = t_end + EXECUTION_MISS_S
                continue
            rec.t_spoken = t_end
            self._deadline[rec.id] = t_end + READBACK_MISS_S
            self._emit("error_spoken", rec.callsign, rec.model_dump(), resp.in_reply_to)

    def tick(self, t: float) -> None:
        for eid, due in sorted(self._deadline.items(), key=lambda kv: kv[1]):
            if t >= due and eid in self.errors:
                self._miss(self.errors[eid], reason="timeout")

    def on_blocked(self, callsign: str, tx_id: str) -> Optional[PilotResponse]:
        if callsign not in self._on_freq():
            return None
        plan = self._plan(callsign, "recall")
        return PilotResponse(callsign=callsign, kind="recall", plan=plan, in_reply_to=tx_id,
                             reaction_delay_s=round(self._rng.uniform(3.0, 6.0), 2))

    def set_error_rate(self, rate: ErrorRate) -> None:
        self.error_rate = rate

    def force_error(self, mode: ErrorMode, kind: Optional[ErrorKind] = None,
                    callsign: Optional[str] = None) -> None:
        self._forced = (mode, kind, callsign)
