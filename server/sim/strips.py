"""Controller's list record ("strip") per aircraft — SIM_SPEC §5.5.

The strip is the controller's memory aid shown in the EuroScope-style lists and tags. It never
moves an aircraft: only a radio instruction does. Two ways to change it:
  * `edit(world, callsign, field, value)` — a manual edit from the lists (control `edit_strip`
    once Bhavin adds it to the contract), validated per field;
  * `autofill(world, tx)` — after each controller transmission, CFL / AHDG / ASPD / DCT / STS /
    ASSR are filled from what the CONTROLLER said (intended values), never from the pilot's
    readback, so planned errors are not given away. Off when world.strip_autofill is False.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional

from server.contracts import Transmission
from server.sim.aircraft import AircraftState
from server.sim.geo import norm_hdg

if TYPE_CHECKING:
    from server.sim.world import World

STATUSES = ("STAR", "HOLD", "VECT", "APP", "EST", "TWR", "SID", "LON", "QUE")
FIELDS = ("cfl", "ahdg", "aspd", "dct", "sts", "clr", "rwy", "sid", "star", "ades", "rfl",
          "assr", "fr")
_PHASE_STS = {"STAR": "STAR", "HOLD": "HOLD", "VECTORS": "VECT", "APPROACH": "APP",
              "FINAL": "EST", "GO_AROUND": "VECT", "QUEUED": "QUE", "TAKEOFF": "SID",
              "SID": "SID", "HANDED_OFF": "LON", "LANDED": "TWR"}


def _level_ft(value: Any) -> Optional[int]:
    """'A40' / 'A4000' / 'F80' / 'FL080' / 4000 / 80 -> feet; None clears."""
    if value in (None, "", "-"):
        return None
    txt = str(value).upper().replace(" ", "")
    m = re.fullmatch(r"(A|F|FL)?(\d{1,5})", txt)
    if not m:
        raise ValueError(f"bad level {value!r}")
    kind, n = m.group(1), int(m.group(2))
    if kind in ("F", "FL"):
        ft = n * 100
    elif kind == "A":
        ft = n * 100 if n < 1000 else n
    else:
        ft = n if n >= 1000 else n * 100
    if not 1000 <= ft <= 45000:
        raise ValueError(f"level out of range {value!r}")
    return ft


def _validate(w: "World", ac: AircraftState, field: str, value: Any) -> Any:
    if field in ("cfl", "rfl"):
        ft = _level_ft(value)
        return None if ft is None else (ft if field == "cfl" else ft // 100)
    if field == "ahdg":
        if value in (None, ""):
            return None
        h = int(float(value))
        if not 1 <= h <= 360:
            raise ValueError("heading 001-360")
        return h
    if field == "aspd":
        if value in (None, ""):
            return None
        v = int(float(value))
        if not 100 <= v <= 350:
            raise ValueError("speed 100-350")
        return v
    if field == "dct":
        if value in (None, ""):
            return None
        fix = str(value).upper()
        if fix not in w.egkk["fixes"]:
            raise ValueError(f"unknown fix {fix}")
        return fix
    if field == "sts":
        s = str(value or "").upper()
        if s and s not in STATUSES:
            raise ValueError(f"status one of {STATUSES}")
        return s or None
    if field == "clr":
        return bool(value) and str(value).lower() not in ("0", "false", "no")
    if field == "rwy":
        r = str(value).upper()
        if r not in w.egkk["runways"]:
            raise ValueError("unknown runway")
        return r
    if field in ("sid", "star"):
        kind = "SID" if field == "sid" else "STAR"
        p = str(value).upper()
        if p not in w.egkk["procedures"][kind].get(w.runway, {}):
            raise ValueError(f"unknown {kind}")
        return p
    if field == "ades":
        a = str(value).upper()
        if not re.fullmatch(r"[A-Z]{4}", a):
            raise ValueError("ICAO code")
        return a
    if field == "assr":
        sq = str(value)
        if not re.fullmatch(r"[0-7]{4}", sq):
            raise ValueError("4 octal digits")
        return sq
    if field == "fr":
        fr = str(value).upper()[:1]
        if fr not in ("I", "V"):
            raise ValueError("I or V")
        return fr
    raise ValueError(f"unknown field {field}")


def edit(w: "World", callsign: str, field: str, value: Any, by: str = "controller") -> bool:
    """Manual list edit. Returns False (and changes nothing) for a bad callsign/field/value."""
    ac = w.aircraft.get(callsign)
    if ac is None or field not in FIELDS:
        return False
    try:
        v = _validate(w, ac, field, value)
    except (ValueError, TypeError):
        return False
    if v is None:
        ac.strip.pop(field, None)
    else:
        ac.strip[field] = v
    ac.strip.setdefault("_manual", [])
    if field not in ac.strip["_manual"]:
        ac.strip["_manual"].append(field)
    return True


def autofill(w: "World", tx: Transmission) -> None:
    if not w.strip_autofill or not tx.receiver or tx.intent not in (
            "instruction", "readback_correction"):
        return
    ac = w.aircraft.get(tx.receiver)
    if ac is None:
        return
    s = ac.strip
    manual = s.get("_manual", [])
    for ins in tx.instructions:
        v = ins.value
        if ins.type in ("altitude", "maintain_level") and v is not None:
            f = float(v)
            s["cfl"] = int(f if f >= 1000 else f * 100)
        elif ins.type == "flight_level" and v is not None:
            s["cfl"] = int(float(v) * 100)
        elif ins.type == "heading" and v is not None:
            s["ahdg"] = int(float(v)) % 360 or 360
            s.pop("dct", None)
        elif ins.type == "turn_by" and v is not None and ins.turn_dir:
            sign = 1 if ins.turn_dir == "R" else -1
            s["ahdg"] = int(round(norm_hdg(ac.hdg + sign * float(v)))) or 360
            s.pop("dct", None)
        elif ins.type == "speed" and v is not None:
            s["aspd"] = int(float(v))
        elif ins.type == "speed_cancel":
            s.pop("aspd", None)
        elif ins.type in ("direct", "hold") and ins.fix:
            s["dct"] = ins.fix
            s.pop("ahdg", None)
        elif ins.type == "resume_nav":
            s.pop("ahdg", None)
        elif ins.type in ("approach_clearance", "intercept_localiser"):
            s["sts"] = "APP"
        elif ins.type == "contact":
            s["sts"] = "TWR" if ins.unit == "tower" else "LON" if ins.unit == "london" else s.get("sts")
        elif ins.type == "squawk" and v not in (None, "ident"):
            s["assr"] = str(v).split(".")[0].zfill(4)
        else:
            continue
        for f in ("cfl", "ahdg", "aspd", "dct", "sts", "assr"):
            if f in manual and f in s:
                manual.remove(f)                    # a new instruction overrides a manual edit


def view(w: "World", ac: AircraftState) -> dict[str, Any]:
    """What the lists show: the strip, with flight-plan and status defaults filled in."""
    s = {k: v for k, v in ac.strip.items() if not k.startswith("_")}
    s.setdefault("sts", _PHASE_STS.get(ac.phase, "VECT"))
    s.setdefault("rwy", w.runway)
    s.setdefault("fr", ac.flight_rules)
    s.setdefault("ades", ac.ades)
    s.setdefault("rfl", ac.rfl)
    s.setdefault("assr", ac.assigned_squawk)
    s.setdefault("clr", ac.flow == "DEP" and ac.phase not in ("QUEUED",))
    s.setdefault("sid" if ac.flow == "DEP" else "star", ac.route_name)
    return s
