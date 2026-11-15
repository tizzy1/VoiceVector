"""Within-sentence pacing for pilot speech: not every word at the same speed.

Real crews race through the callsign and the familiar words, then slow down on the bit they are
reading off the notepad: a drawn-out "descend..." while they find the level, digits grouped in
pairs ("one zero, one three"), a short gap before a frequency, the odd "uh". Flash v2.5 has no
per-word speed control, so this is done with markup it does honour (measured, Nov 2026):

  * ``word...``                 stretches that word by ~0.2 s ("descend" 0.33 s -> 0.52 s)
  * ``one zero, one three``     a comma inside a digit run lengthens the digit before it
  * ``<break time="0.2s"/>``    an exact pause (shows up in the alignment as zero-length "words")
  * ``uh,``                     a short filler; the word before it lengthens too

Elongated spellings ("fourrr") are normalised away by the model and do nothing, so they are not used.

The shaped text goes ONLY to engines that understand it (``engine.prosody``); captions, the
validator and fallback engines keep the plain ``tts_text``. ``restore_word_times`` maps the
alignment of the shaped text back onto the plain words. Everything is deterministic per
(callsign, text, seed), so repeats hit the TTS cache.
"""
from __future__ import annotations

import random
import re
import zlib
from dataclasses import dataclass
from typing import Optional

NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "niner",
                "tree", "fife", "thousand", "hundred", "decimal"}
DIGITS = NUMBER_WORDS - {"thousand", "hundred", "decimal"}
# Words a pilot leans on while finding the value that follows. Short words ("left...", "contact...
# director") smear into the next word when stretched (Scribe heard "left into", "contact direct"),
# so only words of 5+ letters are stretched, and turn directions never.
LEAD_WORDS = {"descend", "descending", "climb", "climbing", "altitude", "heading",
              "squawk", "squawking", "speed", "reduce", "increase", "direct", "cleared",
              "maintain", "maintaining", "level", "runway", "information", "passing",
              "decimal", "established", "intercept", "hold", "wilco", "roger"}
FILLERS = {"uh", "um", "er"}

# Per persona: chance of each move, and the cap on moves per transmission.
#   draw = stretch a lead word, group = pair up a long digit run, pause = gap before a value,
#   filler = "uh" at the start of a clause.
PROFILES: dict[str, dict[str, float]] = {
    "crisp_airline": {"draw": 0.40, "group": 0.45, "pause": 0.20, "filler": 0.04, "max": 2},
    "calm_longhaul": {"draw": 0.55, "group": 0.55, "pause": 0.25, "filler": 0.08, "max": 3},
    "rushed":        {"draw": 0.20, "group": 0.20, "pause": 0.10, "filler": 0.04, "max": 1},
    "stressed":      {"draw": 0.30, "group": 0.30, "pause": 0.35, "filler": 0.20, "max": 2},
    "student":       {"draw": 0.55, "group": 0.65, "pause": 0.45, "filler": 0.35, "max": 3},
}
EXTRA_S = {"draw": 0.18, "group": 0.08, "filler": 0.30}   # measured added time per move (pause: its own length)
MAX_SPEEDUP = 1.12                                        # never claw back more than this from the rest


@dataclass(frozen=True)
class Shaped:
    text: str                 # what the prosody-aware engine is asked to say
    extra_s: float            # estimated time the moves add
    moves: tuple[str, ...]    # for logs/tests: ("draw:descend", "group:one zero|one three", ...)


def _core(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _is_digit(word: str) -> bool:
    return _core(word) in DIGITS


def _break(seconds: float) -> str:
    return f'<break time="{seconds:.2f}s"/>'


def shape(text: str, persona: str, *, callsign_speech: str = "", seed: int = 0,
          stressed: bool = False) -> Shaped:
    """Return ``text`` with a few persona-dependent slow spots. The callsign clause is never touched."""
    profile = PROFILES["stressed" if stressed else persona] if (stressed or persona in PROFILES) \
        else PROFILES["crisp_airline"]
    rng = random.Random(zlib.crc32(f"{seed}|{callsign_speech}|{text}".encode()))
    clauses = text.split(", ")
    cs = callsign_speech.lower().strip()
    budget = int(profile["max"])
    extra = 0.0
    moves: list[str] = []
    out: list[str] = []
    order = list(range(len(clauses)))
    rng.shuffle(order)                                     # spread the moves, not always clause one
    shaped: dict[int, str] = {}
    cs_at = next((k for k, c in enumerate(clauses) if cs and cs in c.lower()), len(clauses))
    for idx in order:
        clause = clauses[idx]
        if not clause.strip() or (cs and cs in clause.lower()) or "<" in clause:
            shaped[idx] = clause
            continue
        words = clause.split(" ")
        prefix = ""
        # 1. Pair up long digit runs (QNH, squawk, frequency): "one zero, one three".
        runs, i = [], 0
        while i < len(words):
            if _is_digit(words[i]):
                j = i
                while j < len(words) and _is_digit(words[j]):
                    j += 1
                runs.append((i, j))
                i = j
            else:
                i += 1
        for a, b in runs:
            if budget and b - a >= 4 and rng.random() < profile["group"]:
                cut = a + 2 if b - a == 4 else a + (b - a) // 2
                if not words[cut - 1].endswith(","):
                    words[cut - 1] += ","
                    budget -= 1
                    extra += EXTRA_S["group"]
                    moves.append("group:" + " ".join(words[a:cut]).rstrip(","))
        # 2. Stretch the word the pilot leans on before a value ("descend...", "heading...").
        candidates = [k for k, w in enumerate(words[:-1])
                      if _core(w) in LEAD_WORDS and len(_core(w)) >= 5 and not w.endswith(",") and
                      (_is_digit(words[k + 1]) or _core(words[k + 1]) in LEAD_WORDS or k == 0)]
        if budget and candidates and rng.random() < profile["draw"]:
            k = candidates[0] if rng.random() < 0.6 else rng.choice(candidates)
            words[k] += "..."
            budget -= 1
            extra += EXTRA_S["draw"]
            moves.append("draw:" + _core(words[k]))
        # 3. A beat before the value itself, as if reading it off the pad.
        elif budget and runs and rng.random() < profile["pause"]:
            a = runs[0][0]
            if a > 0 and not words[a - 1].endswith((",", "...")):
                seconds = round(rng.uniform(0.15, 0.30), 2)
                words[a - 1] += " " + _break(seconds)
                budget -= 1
                extra += seconds
                moves.append(f"pause:{seconds}")
        # 4. "uh," at the start of a clause that is not the first thing said.
        already = any(_core(w) in FILLERS for w in clauses[idx].split()[:2] + clauses[idx - 1].split()[-1:])
        before_sign_off = idx < cs_at or cs_at == 0       # never "…speedbird one one seven, uh, thanks"
        if budget and idx > 0 and before_sign_off and not already and rng.random() < profile["filler"]:
            prefix = "uh, "
            budget -= 1
            extra += EXTRA_S["filler"]
            moves.append("filler")
        shaped[idx] = prefix + " ".join(words)
    out = [shaped[k] for k in range(len(clauses))]
    return Shaped(", ".join(out), round(extra, 3), tuple(moves))


def _is_tag_start(word: str) -> bool:
    return word.startswith("<")


def restore_word_times(plain: str, spoken: list[tuple[str, float, float]]
                       ) -> Optional[list[tuple[str, float, float]]]:
    """Map (word, t0, t1) of the shaped text onto the words of the plain text; None if they disagree.

    Break tags are skipped; a filler is skipped only when the plain text has no filler there
    (some templates say "uh" themselves).
    """
    words = plain.split()
    out, i, in_tag = [], 0, False
    for w, t0, t1 in spoken:
        if in_tag or _is_tag_start(w):
            in_tag = not w.endswith("/>")
            continue
        if i < len(words) and _core(w) == _core(words[i]):
            out.append((words[i], t0, t1))
            i += 1
        elif _core(w) in FILLERS:
            continue
        else:
            return None
    return out if i == len(words) else None


def compensated_speed(settings: dict, word_count: int, target_wps: float, extra_s: float) -> dict:
    """Speed the rest up a touch so the slow spots do not drag the whole call below the persona pace."""
    if extra_s <= 0 or word_count <= 0 or target_wps <= 0 or "speed" not in settings:
        return settings
    nominal = word_count / target_wps
    factor = min(MAX_SPEEDUP, (nominal + extra_s) / nominal)
    out = dict(settings)
    out["speed"] = round(min(1.2, settings["speed"] * factor), 3)
    return out
