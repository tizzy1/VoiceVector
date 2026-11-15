"""Error policy (VOICE_PIPELINE §10): deterministic, seeded, guarded. Never the LLM."""
from __future__ import annotations

import copy
import random
from typing import Any, Optional

from server.contracts import ClearanceElement, ErrorKind, ErrorMode, Instruction

RATE = {"off": 0.0, "low": 0.05, "med": 0.10, "high": 0.15}
MODE_WEIGHTS: list[tuple[ErrorMode, float]] = [
    ("consistent", 0.5), ("readback_only", 0.25), ("execution_only", 0.25)]
READBACK_KINDS: tuple[ErrorKind, ...] = (
    "wrong_value_adjacent", "digit_transposition", "turn_direction_swap", "level_type_confusion")
EXECUTION_KINDS: tuple[ErrorKind, ...] = (
    "wrong_turn_direction", "wrong_value_flown", "level_bust_overshoot", "delayed_compliance")
MAX_PER_AIRCRAFT = 1
MAX_GLOBAL = 2
GUARD_S = 30
LEVEL_BUST_FT = 300
LEVEL_BUST_RECOVER_S = 20.0


def _flip(d: Optional[str]) -> Optional[str]:
    return {"L": "R", "R": "L"}.get(d or "")


def _transpose(v: int, lo: int, hi: int) -> Optional[int]:
    s = str(v)
    if len(s) < 2:
        return None
    t = int(s[:-2] + s[-1] + s[-2]) if len(s) >= 2 else v
    return t if t != v and lo <= t <= hi else None


def mutate(kind: ErrorKind, ins: Instruction, rng: random.Random) -> Optional[Instruction]:
    """Return the wrong version of `ins` for a value/readback-type error, or None if n/a."""
    v = ins.value
    out = ins.model_copy()
    if kind == "turn_direction_swap" or kind == "wrong_turn_direction":
        if ins.type in ("heading", "turn_by") and ins.turn_dir:
            out.turn_dir = _flip(ins.turn_dir)
            return out
        return None
    if v is None or isinstance(v, str):
        return None
    v = float(v)
    if kind in ("wrong_value_adjacent", "wrong_value_flown"):
        step = {"heading": 10, "altitude": 1000, "flight_level": 10, "speed": 10}.get(ins.type)
        if step is None:
            return None
        nv = v + step * rng.choice((-1, 1))
        if ins.type == "heading":
            nv = (nv - 1) % 360 + 1
        if ins.type in ("altitude", "flight_level") and nv <= 0:
            nv = v + step
        out.value = float(int(nv))          # float: Instruction.value is float|str
        return out
    if kind == "digit_transposition":
        rng_lim = {"heading": (1, 360), "speed": (140, 300)}.get(ins.type)
        if rng_lim is None:
            return None
        t = _transpose(int(v), *rng_lim)
        if t is None:
            return None
        out.value = float(t)
        return out
    if kind == "level_type_confusion":
        if ins.type == "altitude" and v >= 3000 and v % 1000 == 0:
            return out.model_copy(update={"type": "flight_level", "value": float(int(v // 100))})
        if ins.type == "flight_level" and v <= 100:
            return out.model_copy(update={"type": "altitude", "value": float(int(v * 100))})
        return None
    return None


TAKEOVER_SHARE = 0.25          # of the session error rate, when a similar callsign is on frequency


def similar_callsigns(a: str, b: str) -> bool:
    """BAW117 / BAW171 (same airline, digits swapped) or BAW117 / BAW127 (one character off)."""
    if a == b or a[:3] != b[:3] or len(a) != len(b) or len(a) < 5:
        return False
    x, y = a[3:], b[3:]
    return sorted(x) == sorted(y) or sum(c != d for c, d in zip(x, y)) == 1


OMISSION_KINDS: tuple[ErrorKind, ...] = ("omitted_element",)   # slip only -> D3 if not challenged


# 15 Nov (Sanju for Tudor): mistakes should mostly be small: an adjacent or swapped number, or a
# missed instruction. Bigger ones stay possible but rarer. Used to order the kinds tried.
KIND_WEIGHTS: dict[str, float] = {
    "wrong_value_adjacent": 5.0, "digit_transposition": 2.0, "omitted_element": 4.0,
    "wrong_value_flown": 4.0, "turn_direction_swap": 1.0, "level_type_confusion": 0.5,
    "wrong_turn_direction": 1.0, "level_bust_overshoot": 1.0, "delayed_compliance": 1.5,
}


def weighted_order(kinds: list[ErrorKind], rng) -> list[ErrorKind]:
    """A random order of ``kinds`` where small mistakes tend to come first."""
    pool, out = list(kinds), []
    while pool:
        pick = rng.choices(pool, weights=[KIND_WEIGHTS.get(k, 1.0) for k in pool])[0]
        out.append(pick)
        pool.remove(pick)
    return out


def candidates(mode: ErrorMode, kind: Optional[ErrorKind]) -> list[ErrorKind]:
    pool = EXECUTION_KINDS if mode == "execution_only" else READBACK_KINDS
    if mode == "readback_only":
        pool = pool + OMISSION_KINDS
    if kind is not None:
        return [kind]
    return list(pool)


def build(kind: ErrorKind, mode: ErrorMode, el: ClearanceElement,
          rng: random.Random) -> Optional[tuple[ClearanceElement, list[ClearanceElement], Any, Any]]:
    """Transform `el`. Returns (element, extra_apply_elements, readback_value, flown_value)."""
    ins = el.intended
    new = el.model_copy(deep=True)
    extra: list[ClearanceElement] = []
    if kind == "omitted_element":
        if mode != "readback_only":
            return None
        return new, extra, None, _val(ins)          # read back nothing, flown correctly
    if kind == "delayed_compliance":
        new.delay_s = round(rng.uniform(15.0, 25.0), 1)
        return new, extra, _val(ins), _val(ins)
    if kind == "level_bust_overshoot":
        if ins.type not in ("altitude", "flight_level") or ins.value is None:
            return None
        sign = -1 if ins.verb == "descend" else 1
        if ins.type == "altitude":
            bust = ins.model_copy(update={"value": float(ins.value) + sign * LEVEL_BUST_FT})
        else:
            bust = ins.model_copy(update={"type": "altitude",
                                          "value": float(ins.value) * 100 + sign * LEVEL_BUST_FT})
        new.flown = bust
        extra.append(ClearanceElement(type=ins.type, intended=ins, readback=ins, flown=ins,
                                      delay_s=LEVEL_BUST_RECOVER_S + 60.0))
        return new, extra, _val(ins), _val(bust)
    wrong = mutate(kind, ins, rng)
    if wrong is None:
        return None
    if mode == "execution_only":
        new.flown = wrong
    elif mode == "readback_only":
        new.readback = wrong
    else:
        new.readback = new.flown = wrong
    return new, extra, _val(new.readback), _val(new.flown)


def _val(i: Instruction) -> Any:
    if i.type in ("heading", "turn_by") and i.turn_dir:
        return {"value": i.value, "turn_dir": i.turn_dir}
    if i.type == "flight_level":
        return {"FL": i.value}
    return i.value


# --------------------------------------------------------- reaction-window guard
class _NullBus:
    def emit(self, e) -> None: ...
    def subscribe(self, cb) -> None: ...
    def now(self) -> float: return 0.0


class _NoGen:
    backlog = 0

    def tick(self) -> None: ...


def _los_pairs(w, cs: str) -> bool:
    from server.sim.geo import dist
    me = w.aircraft.get(cs)
    if me is None or me.phase == "LANDED":
        return False
    for o in w.aircraft.values():
        if o.callsign == cs or o.phase == "LANDED":
            continue
        if dist(me.x, me.y, o.x, o.y) < w.min_sep_nm and abs(me.alt - o.alt) < w.min_sep_ft:
            return True
    return False


def causes_los(world, cs: str, good: list[ClearanceElement],
               bad: list[ClearanceElement]) -> bool:
    """Fly both versions GUARD_S seconds on a shadow copy; unsafe if only the error loses sep."""
    if not hasattr(world, "aircraft") or not hasattr(world, "_apply"):
        return False

    def run(elements: list[ClearanceElement]) -> bool:
        sh = copy.copy(world)
        sh.bus, sh.generator, sh.dep_generator = _NullBus(), _NoGen(), _NoGen()
        sh.aircraft = copy.deepcopy(world.aircraft)
        sh._pending = list(world._pending)
        sh.alerts = dict(world.alerts)
        sh.schedule_apply(cs, elements, at=sh.t)
        for _ in range(GUARD_S):
            sh.tick(1.0)
            if _los_pairs(sh, cs):
                return True
        return False

    return run(bad) and not run(good)
