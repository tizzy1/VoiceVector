"""Composer: ReadbackPlan -> varied, *validated* spoken text (VOICE_PIPELINE §11).

Style is free, content is verified. The plan's values (planned errors included) are never altered:
numbers only enter the text through slots, and ``validator.validate`` re-parses every candidate
before it is accepted. On repeated failure the canonical template is used (``fallback_used``).

``report_values`` keys understood (reply kinds):
  checkin   level_fl or alt (ft), route/star ("WILLO1G"), atis ("K")   [as emitted by World]
  report    heading, speed, cleared_alt (ft), cleared_fl, level_ft, level_fl, passing_ft,
            passing_fl, established (bool)   [cleared_alt/cleared_fl/heading/speed/established are
            what PilotEngine emits]
  request   request (numeric-free text, e.g. "direct" / "descent")
  mayday/pan  nature (numeric-free text)
  established  runway ("26L")
"""
from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from server.contracts import Instruction, Readback, ReadbackPlan
from server.pilot import validator as V
from server.voice.pronounce import is_spelled_point, respell, spell
from server.pilot.variants_build import (CONNECTORS, DEFAULT_BANK, PARTIAL_LEADS, PERSONA_PREFIX,
                                         PERSONA_STYLE_WEIGHTS, PERSONA_SUFFIX, REASON_PHRASES,
                                         REPLIES)
from server.voice import numbers as N

logger = logging.getLogger(__name__)

_SLOT = re.compile(r"\{(\w+)\}")
_ELEMENT_KINDS = ("full", "partial_correction")
_VERBS = {"climb": ("climb", "climbing"), "descend": ("descend", "descending"),
          "maintain": ("maintain", "maintaining")}
Slots = dict[str, tuple[str, str]]
# reason-value keys spoken as altitudes/rates ("four thousand"); every other number is digit by digit
_ALTITUDE_KEYS = {"alt", "ceiling_ft", "min_alt", "rate", "max_rate"}
# slots whose value carries meaning: a template that drops one would change the readback
_SIGNIFICANT = ("DIR", "HDG", "N", "ALT", "FL", "SPD", "CON", "RATE", "RWY", "APP", "FIX",
                "UNIT", "FREQ", "SQ", "QNH", "WHAT", "RW", "EXPN", "HPA")


def _usable(template: dict, slots: dict) -> bool:
    used = set(_SLOT.findall(template["t"]))
    if not all(slots.get(n, ("", ""))[1] for n in template["needs"]):
        return False
    return all(name in used for name in _SIGNIFICANT if slots[name][1])


@dataclass
class Built:
    pieces: list[V.Piece] = field(default_factory=list)
    display: list[str] = field(default_factory=list)
    variant_ids: list[str] = field(default_factory=list)
    callsigns: int = 0
    chosen: dict = field(default_factory=dict)   # (callsign, element key) -> variant id

    def add(self, piece: V.Piece, display: str) -> None:
        self.pieces.append(piece)
        self.display.append(display)
        if piece.role == "callsign":
            self.callsigns += 1

    def filler(self, text: str) -> None:
        if text:
            self.add(V.Piece("filler", text), text)

    @property
    def speech(self) -> str:
        return _tidy("".join(p.speech for p in self.pieces))

    @property
    def text(self) -> str:
        t = _tidy("".join(self.display))
        return t[:1].upper() + t[1:]


def _tidy(text: str) -> str:
    text = " ".join(text.split())
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"(,\s*){2,}", ", ", text)
    return text.strip(" ,")


def _empty() -> tuple[str, str]:
    return "", ""


class Composer:
    """``Composer(data_dir)``; ``compose(plan) -> Readback`` (ComposerAPI)."""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.telephony_path = self.data_dir / "telephony.json"
        tele = json.loads(self.telephony_path.read_text(encoding="utf-8"))
        self.tele_by_icao = {k: v for k, v in tele.items() if not k.startswith("_") and isinstance(v, str)}
        self.tele_words = V.telephony_map(self.telephony_path)
        # "Alfa" -> "alpha", "X-ray" -> "xray": one token, so the validator can decode the callsign
        self.phonetic = {k: ("alpha" if v.lower() == "alfa" else v.lower().replace("-", ""))
                         for k, v in tele.get("_phonetic", {}).items()}
        self.bank = self._load_bank()
        names_path = self.data_dir / "spoken_names.json"     # Tudor's: SFD -> "Seaford"
        self.navaid_names: dict[str, str] = (
            json.loads(names_path.read_text(encoding="utf-8")).get("navaids", {})
            if names_path.exists() else {})
        self.last_variant: dict[tuple[str, str], str] = {}
        self.last_readback: dict[str, Readback] = {}
        self.rejections = 0

    # ------------------------------------------------------------------ bank
    def _load_bank(self) -> dict:
        path = self.data_dir / "readback_variants.json"
        bank = {"elements": DEFAULT_BANK, "persona_style_weights": PERSONA_STYLE_WEIGHTS,
                "persona_prefix": PERSONA_PREFIX, "persona_suffix": PERSONA_SUFFIX,
                "partial_leads": PARTIAL_LEADS, "connectors": CONNECTORS}
        if path.exists():
            try:
                bank.update({k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items()
                             if k in bank})
                merged = {k: list(v) for k, v in DEFAULT_BANK.items()}
                for etype, templates in bank["elements"].items():
                    # generated phrasings stay out until a human approves them (approved: false)
                    merged[etype] = [t for t in templates if t.get("approved", True) is not False]
                bank["elements"] = merged
            except (OSError, ValueError):
                logger.exception("variant bank unreadable; using built-in defaults")
        return bank

    # ------------------------------------------------------------ navaids
    def fix_speech(self, ident: str) -> tuple[str, str]:
        """(display, speech): navaids by station name ("SFD" -> "Seaford"), RNAV points with digits
        spelled ("KKS25" -> "kilo kilo sierra two five"), other fixes as written."""
        name = self.navaid_names.get(ident.upper())
        if name:
            return name, name.lower()
        if is_spelled_point(ident):
            return ident.upper(), spell(ident)
        return ident, ident.lower()

    # ------------------------------------------------------------- callsigns
    def callsign_speech(self, callsign: str) -> tuple[str, str]:
        """"BAW117" -> ("Speedbird 117", "speedbird one one seven")."""
        cs = callsign.upper()
        prefix, rest = cs[:3], cs[3:]
        if prefix.isalpha() and prefix in self.tele_by_icao and rest:
            name, tail = self.tele_by_icao[prefix], rest
        else:
            name, tail = "", cs
        words = []
        for ch in tail:
            if ch.isdigit():
                words.append(N.SPEECH_DIGIT[int(ch)])
            else:
                words.append(self.phonetic.get(ch, ch.lower()))
        speech = " ".join(([name.lower()] if name else []) + words)
        display = f"{name} {tail}".strip()
        return display, speech

    # ---------------------------------------------------------------- slots
    def slots_for(self, el: Instruction) -> Slots:
        s: Slots = {k: _empty() for k in (
            "DIR", "HDG", "N", "VERB", "ING", "ALT", "FL", "SPD", "CON", "RATE", "RWY", "APP",
            "FIX", "UNIT", "FREQ", "SQ", "QNH", "WHAT", "RW", "EXPN", "HPA", "TO")}
        s["QNHW"] = ("QNH", "Q N H")
        if el.turn_dir:
            word = "left" if el.turn_dir == "L" else "right"
            s["DIR"] = (word, word)
        if el.verb in _VERBS:
            s["VERB"], s["ING"] = ((_VERBS[el.verb][0],) * 2, (_VERBS[el.verb][1],) * 2)
            if el.verb in ("climb", "descend"):
                s["TO"] = ("to", "to")                  # "descend to altitude"; "maintain altitude"
        if el.type == "expedite" and el.verb in ("climb", "descend"):
            s["EXPN"] = ("climb",) * 2 if el.verb == "climb" else ("descent",) * 2
        value = el.value
        if el.type == "heading" and value is not None:
            s["HDG"] = (f"{int(value) % 360 or 360:03d}", N.heading_to_speech(value))
        elif el.type == "turn_by" and value is not None:
            s["N"] = (str(int(value)), N.digits_to_speech(str(int(value))))
        elif el.type in ("altitude", "flight_level", "maintain_level") and value is not None:
            if V._is_fl(el):
                s["FL"] = (str(int(value)), N.digits_to_speech(str(int(value))))
            else:
                s["ALT"] = (str(int(value)), N.altitude_to_speech(int(value)))
        elif el.type == "speed" and value is not None:
            s["SPD"] = (str(int(value)), N.speed_to_speech(int(value)))
            s["CON"] = {"or_greater": ("or greater",) * 2,
                        "or_less": ("or less",) * 2}.get(el.constraint or "", _empty())
        elif el.type == "vertical_rate" and value is not None:
            s["RATE"] = (str(int(value)), N.altitude_to_speech(int(value)))
            s["CON"] = {"max": ("or less",) * 2, "min": ("or greater",) * 2}.get(el.constraint or "", _empty())
        elif el.type == "squawk" and value is not None:
            # A numeric code (e.g. an injected error's int, coerced to 4521.0) is still four digits.
            code = f"{int(value):04d}" if isinstance(value, (int, float)) else str(value)
            s["SQ"] = (code, "ident" if code == "ident" else N.squawk_to_speech(code))
        elif el.type == "qnh" and value is not None:
            s["QNH"] = (str(int(value)), N.qnh_to_speech(int(value)))
            if int(value) < 1000:                       # below 1000 the unit is said, to avoid inHg confusion
                s["HPA"] = ("hectopascals", "hectopascals")
        if el.runway:
            s["RWY"] = (el.runway, N.runway_to_speech(el.runway))
        if el.approach_type:
            s["APP"] = {"ILS": ("ILS", "I L S"), "LOC": ("localiser", "localiser"),
                        "VISUAL": ("visual", "visual"), "RNP": ("RNP", "R N P")}[el.approach_type]
        elif el.type == "descend_glidepath":
            pass
        if el.fix:
            s["FIX"] = self.fix_speech(el.fix)
        if el.unit:
            s["UNIT"] = (el.unit, {"london": "london control"}.get(el.unit, el.unit))
        if el.frequency:
            s["FREQ"] = (el.frequency, N.frequency_to_speech(el.frequency))
        if el.type in ("report", "confirm") and el.report_what:
            s["RW"] = (el.report_what, {"level": "level"}.get(el.report_what, el.report_what))
        if el.type == "expect" and el.expect_what:
            what = el.expect_what
            if what.upper().startswith("ILS") and el.expect_what[3:].strip():
                rwy = el.expect_what[3:].strip().upper()
                s["WHAT"] = (what, f"I L S runway {N.runway_to_speech(rwy)}")
            elif what.upper().startswith("FL") and what[2:].isdigit():
                s["WHAT"] = (what, f"flight level {N.digits_to_speech(what[2:])}")
            elif not any(c.isdigit() for c in what):
                s["WHAT"] = (what, what.lower())
        return s

    @staticmethod
    def bank_key(el: Instruction) -> Optional[str]:
        if el.type == "maintain_level":
            return "flight_level" if V._is_fl(el) else "altitude"
        return el.type

    # ------------------------------------------------------------- rendering
    @staticmethod
    def render_phrase(tpl: str, slots: Slots) -> tuple[str, str, list[str]]:
        used: list[str] = []

        def speech(m):
            d, sp = slots[m.group(1)]
            if sp:
                used.append(sp)
            return sp

        sp = _tidy(_SLOT.sub(speech, tpl))
        disp = _tidy(_SLOT.sub(lambda m: slots[m.group(1)][0], tpl))
        return disp, sp, used

    # ----------------------------------------------------------------- build
    def _build_elements(self, plan: ReadbackPlan, rng: random.Random, canonical: bool,
                        skip_ids: set[str]) -> Optional[Built]:
        persona = plan.persona if plan.persona in PERSONA_STYLE_WEIGHTS else "crisp_airline"
        elements = list(plan.elements)
        if not elements:
            return None
        if not canonical and len(elements) > 1 and rng.random() < 0.3:
            rng.shuffle(elements)
        b = Built()
        cs_display, cs_speech = self.callsign_speech(plan.callsign)
        # Readbacks: callsign at the end for the large majority (ICAO style); a few crews lead with it.
        placement = "end" if canonical else rng.choices(["end", "start", "both"], [0.90, 0.08, 0.02])[0]
        prefix = "" if canonical else rng.choice(self.bank["persona_prefix"].get(persona, [""]))
        lead = ""
        if plan.kind == "partial_correction" and not canonical:
            lead = rng.choice(self.bank["partial_leads"])

        def callsign_piece():
            b.add(V.Piece("callsign", cs_speech, slot_speeches=[cs_speech]), cs_display)

        b.filler(prefix + lead)
        if placement in ("start", "both"):
            callsign_piece()
            b.filler(", ")
        for idx, el in enumerate(elements):
            key = self.bank_key(el)
            slots = self.slots_for(el)
            tpl = None
            candidates = [t for t in self.bank["elements"].get(key, [])
                          if t["id"] not in skip_ids and _usable(t, slots)]
            if candidates:
                tpl = (candidates[0] if canonical else
                       self._pick_from(candidates, key, persona, plan.callsign, rng))
            if tpl is None:
                return None
            disp, sp, used = self.render_phrase(tpl["t"], slots)
            b.add(V.Piece("element", sp, el, used), disp)
            b.variant_ids.append(tpl["id"])
            b.chosen[(plan.callsign, key)] = tpl["id"]
            if idx < len(elements) - 1:
                b.filler(", " if canonical else rng.choice(self.bank["connectors"]))
        if placement in ("end", "both"):
            b.filler(", ")
            callsign_piece()
        if not canonical:
            b.filler(rng.choice(self.bank["persona_suffix"].get(persona, [""])))
        return b

    def _pick_from(self, candidates: list[dict], key: str, persona: str, callsign: str,
                   rng: random.Random) -> dict:
        last = self.last_variant.get((callsign, key))
        pool = [t for t in candidates if t["id"] != last] or candidates
        weights = self.bank["persona_style_weights"].get(persona, PERSONA_STYLE_WEIGHTS["crisp_airline"])
        return rng.choices(pool, [max(weights.get(t["style"], 1.0), 0.01) for t in pool])[0]

    def _build_template(self, template: str, slots: dict[str, tuple[str, str]],
                        b: Built, callsign: str) -> None:
        cs_display, cs_speech = self.callsign_speech(callsign)
        pos = 0
        for m in _SLOT.finditer(template):
            b.filler(template[pos:m.start()])
            name = m.group(1)
            if name == "CALLSIGN":
                b.add(V.Piece("callsign", cs_speech, slot_speeches=[cs_speech]), cs_display)
            else:
                disp, sp = slots[name]
                b.add(V.Piece("reply", sp, slot_speeches=[sp] if sp else []), disp)
            pos = m.end()
        b.filler(template[pos:])

    @staticmethod
    def _reason(plan: ReadbackPlan) -> tuple[Optional[str], dict]:
        """Reason code and numbers: from the plan, or from a World pilot call's report_values."""
        code = plan.reason_code or plan.report_values.get("reason_code")
        values = {**{k: v for k, v in plan.report_values.items() if isinstance(v, (int, float))},
                  **plan.reason_values}
        # Interim (Tudor, evaluate.py): QUERY_UNKNOWN_FIX + what=fix_passed until the contract has
        # QUERY_FIX_PASSED; either way the pilot says "confirm direct TUFOZ, we've passed it".
        if code == "QUERY_UNKNOWN_FIX" and values.get("what") == "fix_passed" and values.get("fix"):
            code = "QUERY_FIX_PASSED"
        return code, values

    def _reason_template(self, plan: ReadbackPlan, rng: random.Random, canonical: bool,
                         slots: dict) -> Optional[str]:
        code, values = self._reason(plan)
        options = REASON_PHRASES.get(code or "", [("say again", ())])
        usable = []
        for tpl, needs in options:
            if all((isinstance(values.get(n), (int, float)) and values.get(n)) or
                   (n == "fix" and isinstance(values.get(n), str) and values.get(n)) for n in needs):
                usable.append(tpl)
        if not usable:
            return None
        if isinstance(values.get("fix"), str) and values["fix"]:
            slots["fix"] = self.fix_speech(values["fix"])       # "TUFOZ" / "Seaford" / spelled KKS25
        for key, val in values.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                speech = (N.altitude_to_speech(int(val)) if key in _ALTITUDE_KEYS
                          else N.digits_to_speech(str(int(val))))
                slots[key] = (str(int(val)), speech)
        return usable[0] if canonical else rng.choice(usable)

    @staticmethod
    def _safe_text(text: object, default: str) -> str:
        text = str(text or "").strip()
        return text if text and not any(c.isdigit() for c in text) else default

    def _build_reply(self, plan: ReadbackPlan, rng: random.Random, canonical: bool) -> Optional[Built]:
        kind, rv = plan.kind, plan.report_values
        slots: dict[str, tuple[str, str]] = {"CALLSIGN": ("", "")}
        b = Built()
        if kind in ("unable", "query"):
            reason = self._reason_template(plan, rng, canonical, slots)
            if reason is None:
                return None
            options = REPLIES[kind]
            template = (options[0] if canonical else rng.choice(options)).replace("{REASON}", reason)
            b.variant_ids.append(f"{kind}.{self._reason(plan)[0]}")
        elif kind == "checkin":
            parts = ["Gatwick Director, {CALLSIGN}"]
            if isinstance(rv.get("level_fl"), (int, float)):
                slots["FLV"] = (str(int(rv["level_fl"])), N.digits_to_speech(str(int(rv["level_fl"]))))
                parts.append("flight level {FLV}")
            elif isinstance(rv.get("alt"), (int, float)):
                slots["ALTV"] = (str(int(rv["alt"])), N.altitude_to_speech(int(rv["alt"])))
                parts.append("passing altitude {ALTV}" if rv.get("passing") else "altitude {ALTV}")
                if rv.get("passing") and isinstance(rv.get("cleared_alt"), (int, float)):
                    slots["CLRV"] = (str(int(rv["cleared_alt"])), N.altitude_to_speech(int(rv["cleared_alt"])))
                    parts.append("climbing to altitude {CLRV}")
            if rv.get("star") or rv.get("route"):
                star = str(rv.get("star") or rv.get("route")).upper()
                m = re.fullmatch(r"([A-Z]+)(\d)([A-Z]?)", star)
                if m:
                    word = self.fix_speech(m.group(1))[1]      # "SFD1X" -> "seaford one x-ray"
                    sp = " ".join(filter(None, [word, N.SPEECH_DIGIT[int(m.group(2))],
                                                self.phonetic.get(m.group(3), "")]))
                    slots["STARV"] = (star, sp)
                    parts.append("{STARV}")
            if rv.get("atis"):
                letter = str(rv["atis"]).upper()[:1]
                slots["ATISV"] = (letter, self.phonetic.get(letter, letter.lower()))
                parts.append("information {ATISV}")
            template = ", ".join(parts)
            b.variant_ids.append("checkin")
        elif kind == "report":
            parts = []
            if isinstance(rv.get("heading"), (int, float)):
                slots["HDGV"] = (f"{int(rv['heading']) % 360 or 360:03d}", N.heading_to_speech(rv["heading"]))
                parts.append("heading {HDGV}")
            if isinstance(rv.get("speed"), (int, float)):
                slots["SPDV"] = (str(int(rv["speed"])), N.speed_to_speech(int(rv["speed"])))
                parts.append("speed {SPDV}")
            # "cleared" is for take-off, landing, approach and routes only (Tudor): state the level itself
            for key, phrase in (("cleared_alt", "altitude"), ("cleared_ft", "altitude"),
                                ("passing_ft", "passing altitude"), ("level_ft", "altitude")):
                if isinstance(rv.get(key), (int, float)):
                    slots["ALTV"] = (str(int(rv[key])), N.altitude_to_speech(int(rv[key])))
                    parts.append(phrase + " {ALTV}")
                    break
            for key, phrase in (("cleared_fl", "flight level"), ("passing_fl", "passing flight level"),
                                ("level_fl", "flight level")):
                if isinstance(rv.get(key), (int, float)):
                    slots["FLV"] = (str(int(rv[key])), N.digits_to_speech(str(int(rv[key]))))
                    parts.append(phrase + " {FLV}")
                    break
            if rv.get("established") is True:
                parts.append("established")
            elif rv.get("established") is False:
                parts.append("not yet established")
            if not parts:
                return None
            template = ", ".join(parts) + ", {CALLSIGN}"
            b.variant_ids.append("report")
        elif kind in ("mayday", "pan", "request", "established", "looking", "no_further_clearance",
                      "going_around", "recall", "say_again_request", "standby"):
            options = list(REPLIES.get(kind, []))
            if kind in ("mayday", "pan"):
                nature = self._safe_text(rv.get("nature"), "declaring an emergency")
                slots["NATURE"] = (nature, nature.lower())
            if kind == "request":
                req = self._safe_text(rv.get("request"), "vectors")
                slots["REQUEST"] = (req, req.lower())
            if rv.get("fix"):
                slots["FIXV"] = self.fix_speech(str(rv["fix"]))
            if kind == "established":
                rwy = (rv.get("runway") or next((e.runway for e in plan.elements if e.runway), None))
                if rwy:
                    slots["RWYPHRASE"] = (f"runway {rwy}", f"runway {N.runway_to_speech(str(rwy))}")
                else:
                    options = [o for o in options if "RWYPHRASE" not in o]
            options = [o for o in options if all(slots.get(n, ("", ""))[1] or n == "CALLSIGN"
                                                 for n in _SLOT.findall(o))]
            if not options:
                return None
            idx = 0 if canonical else rng.randrange(len(options))
            template = options[idx]
            b.variant_ids.append(f"{kind}.{idx}")
        else:
            return None
        self._build_template(template, slots, b, plan.callsign)
        return b

    # --------------------------------------------------------------- compose
    def _check(self, plan: ReadbackPlan, b: Built) -> list[str]:
        problems = V.validate(plan.callsign, b.pieces, b.speech, self.tele_words, b.callsigns)
        if plan.kind in _ELEMENT_KINDS:
            if b.callsigns < 1:
                problems.append("callsign missing")
            spoken = [p.element for p in b.pieces if p.role == "element"]
            if len(spoken) != len(plan.elements):
                problems.append("element count differs from plan")
        return problems

    def compose(self, plan: ReadbackPlan) -> Readback:
        if plan.kind == "silence":
            return Readback(text="", tts_text="", validated=True)
        if plan.kind == "repeat_last":
            last = self.last_readback.get(plan.callsign)
            if last is not None:
                return last.model_copy(update={"variant_ids": ["repeat_last"]})
            if plan.elements:
                plan = plan.model_copy(update={"kind": "full"})
            else:
                plan = ReadbackPlan.model_construct(**{**plan.__dict__, "kind": "standby"})  # internal kind
        rng = random.Random(f"{plan.seed}|{plan.callsign}|{plan.kind}|{len(plan.elements)}|{plan.persona}")
        for _ in range(6):
            b = self._candidate(plan, rng, canonical=False)
            if b is None:
                break
            problems = self._check(plan, b)
            if not problems:
                return self._finish(plan, b, fallback=False, validated=True)
            self.rejections += 1
            logger.warning("readback rejected for %s (%s): %s", plan.callsign, b.variant_ids, problems)
        skip: set[str] = set()
        for _ in range(8):
            b = self._candidate(plan, rng, canonical=True, skip_ids=skip)
            if b is None:
                break
            problems = self._check(plan, b)
            if not problems:
                return self._finish(plan, b, fallback=True, validated=True)
            logger.error("canonical readback rejected for %s: %s", plan.callsign, problems)
            skip.update(b.variant_ids)
        logger.error("no valid readback for plan %s", plan.model_dump(mode="json"))
        return Readback(text="Say again", tts_text="say again", variant_ids=[], validated=False,
                        fallback_used=True)

    def _candidate(self, plan: ReadbackPlan, rng: random.Random, canonical: bool,
                   skip_ids: Optional[set[str]] = None) -> Optional[Built]:
        if plan.kind in _ELEMENT_KINDS:
            return self._build_elements(plan, rng, canonical, skip_ids or set())
        return self._build_reply(plan, rng, canonical)

    def _finish(self, plan: ReadbackPlan, b: Built, fallback: bool, validated: bool) -> Readback:
        self.last_variant.update(b.chosen)
        # ICAO respellings ("leema") only now, after validation of the plain speech (§11.1)
        result = Readback(text=b.text, tts_text=respell(b.speech), variant_ids=b.variant_ids,
                          validated=validated, fallback_used=fallback)
        self.last_readback[plan.callsign] = result
        return result
