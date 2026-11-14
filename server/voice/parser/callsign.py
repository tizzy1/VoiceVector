"""Callsign resolution: fuzzy-match transcript spans against aircraft on the scope only."""
from __future__ import annotations

from rapidfuzz import fuzz

from server.contracts import CallsignInfo
from server.voice.normalise import normalise_tokens
from server.voice.numbers import DIGIT_WORDS

PHONETIC = {
    "alpha": "a", "bravo": "b", "charlie": "c", "delta": "d", "echo": "e", "foxtrot": "f",
    "golf": "g", "hotel": "h", "india": "i", "juliett": "j", "juliet": "j", "kilo": "k",
    "lima": "l", "mike": "m", "november": "n", "oscar": "o", "papa": "p", "quebec": "q",
    "romeo": "r", "sierra": "s", "tango": "t", "uniform": "u", "victor": "v",
    "whiskey": "w", "xray": "x", "yankee": "y", "zulu": "z",
}
TELEPHONY_ALIASES = {"easyjet": "easy"}
_SKIP = {"uh", "um", "er", "the", "please"}

RESOLVED = 0.90   # >= resolved
LOW_CONF = 0.70   # >= resolved but flagged "callsign_low_conf"; below -> unresolved

# Demo scenario (golden-set scope). The live session passes ``world.callsigns()`` instead.
DEMO_CALLSIGNS = [
    CallsignInfo(icao="BAW117", telephony="Speedbird", spoken="speedbird one one seven",
                 spoken_digits="speedbird 117"),
    CallsignInfo(icao="EZY45AB", telephony="Easy", spoken="easy four five alpha bravo",
                 spoken_digits="easy 45 alpha bravo"),
    CallsignInfo(icao="DLH4KA", telephony="Lufthansa", spoken="lufthansa four kilo alpha",
                 spoken_digits="lufthansa 4 kilo alpha"),
    CallsignInfo(icao="UAE15", telephony="Emirates", spoken="emirates one five",
                 spoken_digits="emirates 15"),
    CallsignInfo(icao="WZZ4471", telephony="Wizz Air", spoken="wizz air four four seven one",
                 spoken_digits="wizz air 4471"),
]


def callsign_key(tokens: list[str]) -> str:
    """Compress tokens to a comparable key: "easy four five alpha bravo" -> "easy45ab"."""
    parts = []
    for t in tokens:
        if t in _SKIP:
            continue
        if t in PHONETIC:
            parts.append(PHONETIC[t])
        elif t in DIGIT_WORDS:
            parts.append(DIGIT_WORDS[t])
        else:
            parts.append(TELEPHONY_ALIASES.get(t, t))
    return "".join(parts)


def _forms(info: CallsignInfo) -> set[str]:
    return {
        callsign_key(normalise_tokens(info.spoken)),
        callsign_key(normalise_tokens(info.spoken_digits)),
        info.icao.lower(),
    }


class CallsignMatch:
    def __init__(self, start: int, end: int, ratio: float, info: CallsignInfo):
        self.start, self.end, self.ratio, self.info = start, end, ratio, info


def resolve_callsign(tokens: list[str], callsigns: list[CallsignInfo],
                     max_span: int = 7) -> CallsignMatch | None:
    """Best contiguous span of ``tokens`` (head or tail of the transmission) vs scope aircraft."""
    forms = {c.icao: _forms(c) for c in callsigns}
    best: CallsignMatch | None = None
    for i in range(len(tokens)):
        for j in range(i + 1, min(len(tokens), i + max_span) + 1):
            key = callsign_key(tokens[i:j])
            if not key:
                continue
            for c in callsigns:
                ratio = max(fuzz.ratio(key, f) for f in forms[c.icao]) / 100
                if best is None or ratio > best.ratio:
                    best = CallsignMatch(i, j, ratio, c)
    if best is None or best.ratio < LOW_CONF:
        return None
    return best
