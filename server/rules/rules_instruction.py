"""B-group: controller instruction legality, judged at transmission time (SIM_SPEC §3.2 B)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from server.contracts import Evaluation, Transmission
import math

from server.sim.airspace import TRANSITION_ALT_FT, area_minimum_ft, in_cas
from server.sim.aircraft import AircraftState
from server.sim.evaluate import GS_ABOVE_TOL_FT, _intercept_heading
from server.sim.geo import hdg_diff
from server.sim.modes import glidepath_alt, loc_geometry

from server.rules.common import LEVEL_TYPES, cleared_level_ft

if TYPE_CHECKING:
    from server.rules.engine import RulesEngine

INTERCEPT_WARN_DEG = 30.0          # B4 warn
INTERCEPT_FAIL_DEG = 45.0          # B4 fail
FAF_ALT_FT = 2000.0                # ⚠ VERIFY: FI26L assumed where the 3° glidepath meets 2,000 ft
LATE_INTERCEPT_NM = 2.0            # B4: intercept closer than this before FI26L
LATE_TRANSFER_NM = 4.0             # B10
CAS_LOOKAHEAD_S = (30, 60, 90, 120)  # B2: straight-line projection points along the new heading
DIRECTOR_TOP_FT = 6500.0           # B3: KKAPP 0-5,500 + SPT to 6,500 (⚠ VERIFY intent)
B9_APPLY_S = 3.0                   # B9: readback + reaction before the clearance takes effect
B9_P = 0.5                         # B9: forecast probability that counts as "into conflict"
LATERAL = ("heading", "turn_by", "present_heading", "direct", "hold", "resume_nav")
FAMILIES = {"level": LEVEL_TYPES, "lateral": LATERAL,
            "speed": ("speed", "speed_min_clean", "speed_cancel")}


class InstructionRules:
    def __init__(self, eng: "RulesEngine") -> None:
        self.eng = eng
        self.qnh_given: set[str] = set()
        self.late_transfer: set[str] = set()

    def faf_nm(self) -> float:
        w = self.eng.world
        return (FAF_ALT_FT - w.rwy_elev) / w.gs_ft_per_nm

    def on_tx(self, tx: Transmission, evals: list[Evaluation], ac: AircraftState) -> None:
        ev = {"tx_id": tx.tx_id, "transcript": tx.raw_text or tx.norm_text,
              "state": {"alt": round(ac.alt), "hdg": round(ac.hdg), "ias": round(ac.ias),
                        "phase": ac.phase}}
        cs = ac.callsign
        types = {i.type for i in tx.instructions}
        if "qnh" in types:
            self.qnh_given.add(cs)
        self._b1(tx, ac, types, ev)
        self._b4(tx, ac, ev)
        self._from_evals(tx, evals, ac, ev)
        self._b6_qnh(tx, ac, types, ev)
        self._b2(tx, ac, ev)
        self._b3(tx, ac, types, ev)
        self._b8(tx, ac, ev)
        self._b9(tx, ac, ev)

    # B2 vectoring out of controlled airspace ----------------------------------
    def _b2(self, tx, ac, ev) -> None:
        hdg = None
        for ins in tx.instructions:
            if ins.type == "heading" and ins.value is not None:
                hdg = float(ins.value)
            elif ins.type == "turn_by" and ins.value is not None and ins.turn_dir:
                hdg = ac.hdg + (1 if ins.turn_dir == "R" else -1) * float(ins.value)
        if hdg is None:
            return
        w = self.eng.world
        lvl = next((x for x in (cleared_level_ft(i) for i in tx.instructions
                                if i.type in LEVEL_TYPES) if x is not None), ac.cleared_alt)
        if not in_cas(w, ac.x, ac.y, ac.alt):
            return                                       # already outside: A5's business
        r = math.radians(hdg)
        for s in CAS_LOOKAHEAD_S:
            d = ac.gs * s / 3600.0
            x, y = ac.x + d * math.sin(r), ac.y + d * math.cos(r)
            if not in_cas(w, x, y, lvl):
                self.eng.add(
                    "B2", "MEDIUM", "CONTROLLER", [ac.callsign], t=tx.t, tx_id=tx.tx_id,
                    title=f"Vectored out of controlled airspace: {ac.callsign}",
                    detail=f"Heading {round(hdg) % 360 or 360:03d} at {int(lvl)} ft leaves "
                           f"controlled airspace in about {s} s.",
                    correct_practice="Keep vectors inside controlled airspace at the cleared "
                                     "level, or climb the aircraft before turning it out.",
                    evidence={**ev, "after_s": s, "level": int(lvl)})
                return

    # B3 level outside Director jurisdiction ------------------------------------
    def _b3(self, tx, ac, types, ev) -> None:
        if any(i.type == "contact" and i.unit == "london" for i in tx.instructions):
            return                                       # handoff level with transfer: fine
        for ins in tx.instructions:
            lvl = cleared_level_ft(ins) if ins.type in LEVEL_TYPES else None
            if lvl is None or lvl <= DIRECTOR_TOP_FT or lvl <= ac.alt + 50:
                continue                                 # only climbs above Director airspace
            self.eng.add(
                "B3", "MEDIUM", "CONTROLLER", [ac.callsign], t=tx.t, tx_id=tx.tx_id,
                title=f"Level outside Director airspace: {ac.callsign}",
                detail=f"Climbed to {int(lvl)} ft; Gatwick Director owns up to "
                       f"{int(DIRECTOR_TOP_FT)} ft.",
                correct_practice="Climb above Director airspace only with London's agreement, "
                                 "or transfer the aircraft: 'contact London Control …'.",
                evidence={**ev, "level": int(lvl)})
            return

    # B8 conflicting instructions in one transmission ---------------------------
    def _b8(self, tx, ac, ev) -> None:
        if tx.self_corrections:
            return
        for fam, members in FAMILIES.items():
            got = [i for i in tx.instructions if i.type in members]
            sig = {(i.type, str(i.value), i.turn_dir, i.fix) for i in got}
            if len(got) > 1 and len(sig) > 1:
                said = ", ".join(
                    f"{i.type.replace('_', ' ')} "
                    f"{i.value if i.value is not None else i.fix or ''}".strip() for i in got)
                self.eng.add(
                    "B8", "MEDIUM", "CONTROLLER", [ac.callsign], t=tx.t, tx_id=tx.tx_id,
                    title=f"Conflicting instructions: {ac.callsign}",
                    detail=f"More than one {fam} instruction in one transmission ({said}).",
                    correct_practice="Give one instruction of each kind; to change it, say "
                                     "'correction' and the new value.",
                    evidence={**ev, "family": fam})
                return

    # B9 instruction into conflict (forecast what-if) ---------------------------
    def _b9(self, tx, ac, ev) -> None:
        w = self.eng.world
        if not hasattr(w, "forecaster"):
            return
        from server.sim.forecast import forecast
        lateral_or_level = [i for i in tx.instructions
                            if i.type in LEVEL_TYPES or i.type in LATERAL]
        if not lateral_or_level:
            return
        before = {tuple(f["callsigns"]): f["p_conflict"] for f in forecast(w, only=ac.callsign)}
        after = forecast(w, extra=[(B9_APPLY_S, ac.callsign, i) for i in lateral_or_level],
                         only=ac.callsign)
        for f in after:
            key = tuple(f["callsigns"])
            if f["p_conflict"] >= B9_P and before.get(key, 0.0) < B9_P:
                other = key[0] if key[1] == ac.callsign else key[1]
                self.eng.add(
                    "B9", "MEDIUM", "CONTROLLER", [ac.callsign, other], t=tx.t, tx_id=tx.tx_id,
                    title=f"Instruction into conflict: {ac.callsign} / {other}",
                    detail=f"After this instruction a loss of separation with {other} becomes "
                           f"likely ({f['p_conflict']:.0%} of forecast runs, median "
                           f"{f['t_median_s']:.0f} s); before it, {before.get(key, 0.0):.0%}.",
                    correct_practice="Check the new heading or level against nearby traffic "
                                     "before you give it.",
                    evidence={**ev, "p_before": before.get(key, 0.0),
                              "p_after": f["p_conflict"], "t_median_s": f["t_median_s"]})
                return

    # B1 descent below minimum ------------------------------------------------
    def _b1(self, tx, ac, types, ev) -> None:
        if ac.established or ac.approach_cleared or "approach_clearance" in types:
            return
        mn = area_minimum_ft(self.eng.world, ac)
        for ins in tx.instructions:
            if ins.type not in LEVEL_TYPES:
                continue
            lvl = cleared_level_ft(ins)
            if lvl is not None and lvl < mn:
                self.eng.add(
                    "B1", "HIGH", "CONTROLLER", [ac.callsign], t=tx.t, tx_id=tx.tx_id,
                    title=f"Descent below minimum altitude: {ac.callsign}",
                    detail=f"Cleared to {int(lvl)} ft where the minimum is {mn} ft and the "
                           f"aircraft is not cleared for the approach.",
                    correct_practice=f"Keep the aircraft at or above {mn} ft until it is "
                                     f"established or cleared for the approach.",
                    evidence=ev)
                return

    # B4 bad ILS intercept ------------------------------------------------------
    def _b4(self, tx, ac, ev) -> None:
        if ac.established:
            return
        if not any(i.type in ("approach_clearance", "intercept_localiser")
                   for i in tx.instructions):
            return
        w = self.eng.world
        angle = abs(hdg_diff(_intercept_heading(tx, ac), w.loc_course))
        along, _ = loc_geometry(w, ac)
        add = lambda sev, detail, extra: self.eng.add(  # noqa: E731
            "B4", sev, "CONTROLLER", [ac.callsign], t=tx.t, tx_id=tx.tx_id,
            title=f"Bad ILS intercept: {ac.callsign}", detail=detail,
            correct_practice="Vector to intercept at 30° or less, at or below the glidepath, "
                             "at least 2 NM before the final approach fix.",
            evidence={**ev, **extra})
        if angle > INTERCEPT_FAIL_DEG:
            add("HIGH", f"Intercept angle {angle:.0f}° (more than {INTERCEPT_FAIL_DEG:.0f}°).",
                {"angle": round(angle)})
        elif angle > INTERCEPT_WARN_DEG:
            add("MEDIUM", f"Intercept angle {angle:.0f}° (more than {INTERCEPT_WARN_DEG:.0f}°).",
                {"angle": round(angle)})
        ils = any(i.type == "approach_clearance" and i.approach_type in (None, "ILS")
                  for i in tx.instructions)
        if ils and along > 0:
            gp = glidepath_alt(w, along)
            lvl = min([ac.alt] + [x for x in (cleared_level_ft(i) for i in tx.instructions
                                              if i.type in LEVEL_TYPES) if x is not None])
            if lvl > gp + GS_ABOVE_TOL_FT:
                add("HIGH", f"Aircraft above the glidepath: {int(round(lvl, -2))} ft where the "
                            f"glidepath is {int(round(gp, -2))} ft.",
                    {"alt": round(lvl), "gp_alt": round(gp)})
        faf = self.faf_nm()
        if 0 < along < faf + LATE_INTERCEPT_NM:
            add("MEDIUM", f"Intercept {along:.1f} NM from the threshold — less than "
                          f"{LATE_INTERCEPT_NM:.0f} NM before the final approach fix.",
                {"along_nm": round(along, 1), "faf_nm": round(faf, 1)})

    # B5 / B6 / B7 from the sim's own evaluation of the transmission -------------
    def _from_evals(self, tx, evals, ac, ev) -> None:
        for e in evals:
            ins = tx.instructions[e.instr_idx] if e.instr_idx < len(tx.instructions) else None
            code = e.reason_code
            if code in ("UNABLE_SPEED_LOW", "UNABLE_SPEED_HIGH") and ins is not None \
                    and ins.type == "speed":
                self.eng.add(
                    "B5", "MEDIUM", "CONTROLLER", [ac.callsign], t=tx.t, tx_id=tx.tx_id,
                    title=f"Speed outside envelope: {ac.callsign}",
                    detail=f"Speed {ins.value} kt — {'too slow' if code.endswith('LOW') else 'too fast'}"
                           f" ({e.reason_values}).",
                    correct_practice="Stay at or above the minimum clean speed until the "
                                     "aircraft is configured, and at or below 250 kt below FL100 "
                                     "unless the restriction is cancelled.",
                    evidence={**ev, **e.reason_values})
            elif code == "QUERY_LEVEL_TYPE":
                self.eng.add(
                    "B6", "MEDIUM", "CONTROLLER", [ac.callsign], t=tx.t, tx_id=tx.tx_id,
                    title=f"Transition altitude/level misuse: {ac.callsign}",
                    detail=f"{ins.type.replace('_', ' ') if ins else 'level'} {ins.value if ins else ''}"
                           f" — {e.reason_values}.",
                    correct_practice=f"Use 'altitude' at or below {TRANSITION_ALT_FT} ft and "
                                     f"'flight level' at or above the transition level.",
                    evidence={**ev, **e.reason_values})
            elif code == "QUERY_WRONG_POSITION":
                self.eng.add(
                    "B7", "MEDIUM", "CONTROLLER", [ac.callsign], t=tx.t, tx_id=tx.tx_id,
                    title=f"Wrong-position instruction: {ac.callsign}",
                    detail=f"'{ins.type.replace('_', ' ') if ins else ''}' is Tower's job, "
                           f"not the approach controller's.",
                    correct_practice="Transfer the aircraft to Tower for landing or take-off "
                                     "clearance.",
                    evidence=ev)

    def _b6_qnh(self, tx, ac, types, ev) -> None:
        if "altitude" not in types or ac.callsign in self.qnh_given:
            return
        self.qnh_given.add(ac.callsign)                 # flag once per aircraft
        self.eng.add(
            "B6", "MEDIUM", "CONTROLLER", [ac.callsign], t=tx.t, tx_id=tx.tx_id,
            title=f"First altitude clearance without QNH: {ac.callsign}",
            detail="The aircraft was cleared to an altitude for the first time without a QNH.",
            correct_practice="Give the QNH with the first clearance to an altitude, e.g. "
                             "'descend altitude 4000 feet, QNH 1013'.",
            evidence=ev)

    # B10 late transfer (tick) ---------------------------------------------------
    def tick(self, t: float, acs: list[AircraftState]) -> None:
        w = self.eng.world
        for a in acs:
            if a.flow == "DEP" and a.callsign not in self.late_transfer:
                from server.sim.runway import NOT_TRANSFERRED_NM, not_transferred
                if not_transferred(w, a):
                    self.late_transfer.add(a.callsign)
                    self.eng.add(
                        "B10", "MEDIUM", "CONTROLLER", [a.callsign], t=t,
                        title=f"Departure not transferred: {a.callsign}",
                        detail=f"Reached the end of the {a.route_name} SID or "
                               f"{NOT_TRANSFERRED_NM:g} NM still on the Director frequency.",
                        correct_practice="Transfer departures to London before the sector "
                                         "boundary, e.g. 'contact London Control 134.125'.")
                continue
            if a.flow != "ARR" or a.callsign in self.late_transfer or not a.on_frequency:
                continue
            if a.freq_unit != "director" or not a.established:
                continue
            along, _ = loc_geometry(w, a)
            if 0 < along < LATE_TRANSFER_NM:
                self.late_transfer.add(a.callsign)
                self.eng.add(
                    "B10", "MEDIUM", "CONTROLLER", [a.callsign], t=t,
                    title=f"Late transfer to Tower: {a.callsign}",
                    detail=f"Still on the approach frequency {along:.1f} NM from touchdown.",
                    correct_practice="Transfer arrivals to Tower before 4 NM, e.g. 'contact "
                                     "Gatwick Tower 124.225'.")
