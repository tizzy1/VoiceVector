"""What the TTS is given (VOICE_PIPELINE §11.1): ICAO respellings and spelled RNAV points.

Display text keeps standard spellings. Only ``tts_text`` is respelled, AFTER the validator has
checked the plain speech; the normaliser maps every respelling back (``UNRESPELL``), so re-parsing
the TTS text, caption alignment and STT of our own audio all still work.

``RESPELL`` holds only the letters whose plain-English reading differs from ICAO and that Tudor
has approved by ear (``python -m server.voice.pronounce_check``). ``CANDIDATES`` are respellings
offered on the check page for the others; move one into ``RESPELL`` once approved.
Respellings are single lowercase words, so word counts and alignment never shift.
"""
from __future__ import annotations

import re

from server.voice.numbers import SPEECH_DIGIT

ICAO_WORD = {
    "A": "alpha", "B": "bravo", "C": "charlie", "D": "delta", "E": "echo", "F": "foxtrot",
    "G": "golf", "H": "hotel", "I": "india", "J": "juliett", "K": "kilo", "L": "lima", "M": "mike",
    "N": "november", "O": "oscar", "P": "papa", "Q": "quebec", "R": "romeo", "S": "sierra",
    "T": "tango", "U": "uniform", "V": "victor", "W": "whiskey", "X": "xray", "Y": "yankee", "Z": "zulu",
}

# Active: ICAO says LEE-mah and keh-BECK; English TTS says LIE-mah and KWEH-beck.
RESPELL: dict[str, str] = {
    "lima": "leema",
    "quebec": "kebeck",
}
# Offered on the pronunciation check page; not used until approved.
CANDIDATES: dict[str, list[str]] = {
    "papa": ["pahpah"],          # "pahPAH" was read as "Papa H": capitals are spelled out
    "hotel": ["hohtell"],
    "sierra": ["seeairah"],
    "juliett": ["jewleeett"],
    "uniform": ["younieform"],
    "alpha": ["alfah"],
    "xray": ["ecksray"],
    "whiskey": ["wisskey"],
    "oscar": ["osscah"],
    "romeo": ["rowmeoh"],
}
UNRESPELL: dict[str, str] = {v.lower(): k for k, v in RESPELL.items()}
for _word, _alts in CANDIDATES.items():
    for _alt in _alts:
        UNRESPELL.setdefault(_alt.lower(), _word)

_WORD = re.compile(r"[A-Za-z]+")


def respell(text: str) -> str:
    """Apply the approved respellings to speech text, keeping the original capitalisation style."""
    def sub(m: re.Match) -> str:
        word = m.group(0)
        new = RESPELL.get(word.lower())
        if new is None:
            return word
        return new.capitalize() if word[:1].isupper() else new
    return _WORD.sub(sub, text)


def is_spelled_point(ident: str) -> bool:
    """RNAV/DME points with digits (KKS25, DET31, D068T) are spelled, not read as a word."""
    ident = ident.upper()
    return ident.isalnum() and any(c.isdigit() for c in ident) and any(c.isalpha() for c in ident)


def spell(ident: str) -> str:
    """"KKS25" -> "kilo kilo sierra two five" (plain ICAO words; ``respell`` runs later)."""
    out = []
    for ch in ident.upper():
        if ch.isdigit():
            out.append(SPEECH_DIGIT[int(ch)])
        elif ch in ICAO_WORD:
            out.append(ICAO_WORD[ch])
    return " ".join(out)
