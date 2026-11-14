"""B-group: controller instruction legality, judged at transmission time (SIM_SPEC §3.2 B)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from server.contracts import Evaluation, Transmission
from server.sim.airspace import TRANSITION_ALT_FT, area_minimum_ft
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
