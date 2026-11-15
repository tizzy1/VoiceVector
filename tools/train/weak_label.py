"""Weak-label real controller lines and generate synthetic Gatwick lines (BIO tags).

    .venv/bin/python -m tools.train.weak_label

Real lines (data/train/raw.jsonl, controller only): normalise with the EXISTING normaliser, find
the callsign (airline words + a number/phonetic group, at the start or the end), drop greetings
and station names ("praha radar", "good morning"), then run the EXISTING grammar clause by clause.
A line is kept only if the callsign was found, at least one instruction parsed, nothing was left
unparsed and every clause gave exactly one in-range instruction. Labels = CALLSIGN + the InstrType
of each clause (upper case), so they come from the contract, not invented.

Synthetic lines: 3,000 Gatwick transmissions built from telephony.json callsigns, egkk.json fixes
and valid values, labelled by construction, normalised the same way as at inference.

Split: real by recording (90/10, no recording in both), synthetic 90/10; dev_real = real dev.
Any line whose normalised text is in the parser golden set (or data/golden_real.jsonl) is kept
out of training. Writes data/train/{labelled,rejected,train,dev,dev_real,golden}.jsonl + labels.json.
"""
from __future__ import annotations

import json
import random
import re
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.voice import numbers as N  # noqa: E402
from server.voice.ml_tagger import CUE_SET, NUMBER_WORDS, PHONETIC_WORDS  # noqa: E402
from server.voice.normalise import normalise_tokens  # noqa: E402
from server.voice.parser import default_fixes  # noqa: E402
from server.voice.parser.grammar import build_clauses, build_instructions, out_of_range  # noqa: E402

OUT = ROOT / "data" / "train"
GOLDEN_PARSE = ROOT / "server" / "tests" / "voice" / "golden_parse.jsonl"
GOLDEN_REAL = ROOT / "data" / "golden_real.jsonl"
N_SYNTHETIC = 3000

GREETINGS = {"good", "morning", "afternoon", "evening", "day", "bye", "goodbye", "hello",
             "servus", "ok", "okay", "roger", "thank", "thanks", "you", "ahoj", "dobry", "den",
             "cau", "nashledanou", "na", "shledanou", "now", "and", "then", "please", "the"}
STATIONS = {"radar", "tower", "director", "control", "ground", "information", "delivery"}
NOT_AIRLINE = CUE_SET | GREETINGS | STATIONS | NUMBER_WORDS | PHONETIC_WORDS | {
    "runway", "wind", "traffic", "information", "squawk", "identified", "contact"}


def _is_num(t: str) -> bool:
    return t in NUMBER_WORDS or t.isdigit()


def _is_group(t: str) -> bool:
    return _is_num(t) or t in PHONETIC_WORDS or (len(t) == 1 and t.isalpha())


def find_callsign(tok: list[str]) -> tuple[int, int] | None:
    """Leading (or trailing) airline word(s) + >= 2 number/phonetic tokens -> (start, end)."""
    def scan(start: int) -> tuple[int, int] | None:
        i = start
        while i < len(tok) and i - start < 3 and tok[i] not in NOT_AIRLINE and tok[i].isalpha() \
                and len(tok[i]) > 1:
            i += 1
        if i == start:
            return None
        j = i
        while j < len(tok) and _is_group(tok[j]) and j - i < 7:
            j += 1
        return (start, j) if j - i >= 2 and any(_is_num(t) for t in tok[i:j]) else None
    head = scan(0)
    if head:
        return head
    for k in range(max(0, len(tok) - 9), len(tok)):          # callsign at the end
        hit = scan(k)
        if hit and hit[1] == len(tok):
            return hit
    return None


def label_real(text: str, fixes: set[str]) -> tuple[list[str], list[str]] | str:
    tok = normalise_tokens(text)
    cs = find_callsign(tok)
    if cs is None:
        return "no_callsign"
    tags = ["O"] * len(tok)
    tags[cs[0]] = "B-CALLSIGN"
    for k in range(cs[0] + 1, cs[1]):
        tags[k] = "I-CALLSIGN"
    keep = []                                                 # (original index, token)
    k = 0
    while k < len(tok):
        if cs[0] <= k < cs[1]:
            k += 1
            continue
        if tok[k] in GREETINGS:
            k += 1
            continue
        if k + 1 < len(tok) and tok[k + 1] in STATIONS and tok[k] not in CUE_SET:
            k += 2                                            # "praha radar", "ruzyne tower"
            continue
        if tok[k] in STATIONS:
            k += 1
            continue
        if tok[k] == "contact" and k > 0 and tok[k - 1] == "radar":
            k += 1                                            # "radar contact" = identified
            continue
        keep.append((k, tok[k]))
        k += 1
    if not keep:
        return "no_instruction"
    words = [t for _, t in keep]
    clauses = build_clauses(words, fixes)
    if not clauses:
        return "no_instruction"
    pos = 0
    n_instr = 0
    for cl in clauses:
        got, corr, unparsed = build_instructions([cl], fixes)
        if unparsed or corr or len(got) != 1 or out_of_range(got[0]) or cl.condition:
            return "unparsed"
        if got[0].type == "contact" and not got[0].frequency:
            return "contact_without_frequency"                # span would absorb stray words
        idx = []
        for w in cl.tokens:                                    # map clause tokens back
            while pos < len(words) and words[pos] != w:
                pos += 1
            if pos == len(words):
                return "unmapped"
            idx.append(keep[pos][0])
            pos += 1
        a, b = idx[0], idx[-1] + 1
        if any(tags[x] != "O" for x in range(a, b)):
            return "overlap"
        label = got[0].type.upper()
        tags[a] = f"B-{label}"
        for x in range(a + 1, b):
            tags[x] = f"I-{label}"
        n_instr += 1
    if n_instr == 0:
        return "no_instruction"
    return tok, tags


# ------------------------------------------------------------------ synthetic Gatwick lines
def synthetic(n: int, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    tele = json.loads((ROOT / "data" / "telephony.json").read_text())
    airlines = [v for k, v in tele.items() if not k.startswith("_") and isinstance(v, str)]
    phon = {k: v.lower().replace("alfa", "alpha").replace("juliett", "juliet").replace("-", "")
            for k, v in tele["_phonetic"].items()}
    fixes = sorted(f for f in json.loads((ROOT / "data" / "egkk.json").read_text())["fixes"]
                   if f.isalpha() and len(f) >= 4)
    dig = lambda s: N.digits_to_speech(str(s))                 # noqa: E731

    def callsign() -> str:
        num = str(rng.randint(1, 999)) if rng.random() < 0.6 else str(rng.randint(1, 99))
        suf = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(2)) \
            if rng.random() < 0.3 else ""
        return " ".join([rng.choice(airlines).lower(), dig(num)] + [phon[c] for c in suf])

    def heading():
        h = rng.randrange(10, 361, rng.choice((5, 10)))
        d = rng.choice(["left", "right", None])
        words = dig(f"{h:03d}")
        if d:
            return "HEADING", rng.choice([f"turn {d} heading {words}", f"{d} heading {words}"])
        return "HEADING", rng.choice([f"fly heading {words}", f"heading {words}"])

    def altitude():
        a = rng.randrange(3000, 6001, 1000)
        verb = rng.choice(["descend", "climb"])
        return "ALTITUDE", rng.choice([f"{verb} altitude {N.altitude_to_speech(a)} feet",
                                       f"{verb} to altitude {N.altitude_to_speech(a)} feet",
                                       f"{verb} altitude {N.altitude_to_speech(a)}"])

    def flight_level():
        fl = rng.randrange(70, 151, 10)
        verb = rng.choice(["descend", "climb"])
        return "FLIGHT_LEVEL", rng.choice([f"{verb} flight level {dig(fl)}",
                                           f"{verb} to flight level {dig(fl)}"])

    def speed():
        s = rng.randrange(160, 251, 10)
        return "SPEED", rng.choice([f"reduce speed {dig(s)} knots", f"speed {dig(s)} knots",
                                    f"increase speed {dig(s)} knots"])

    def qnh():
        return "QNH", f"q n h {dig(rng.randint(990, 1030))}"

    def direct():
        return "DIRECT", rng.choice(["proceed direct", "direct", "route direct"]) + \
            f" {rng.choice(fixes).lower()}"

    def ils():
        rw = rng.choice(["26L", "08R"])
        return "APPROACH_CLEARANCE", rng.choice(
            [f"cleared i l s approach runway {N.runway_to_speech(rw)}",
             f"cleared ils approach runway {N.runway_to_speech(rw)}"])

    def squawk():
        return "SQUAWK", "squawk " + " ".join(dig(str(rng.randint(0, 7))) for _ in range(4))

    def contact():
        return "CONTACT", rng.choice([
            "contact gatwick tower one two four decimal two two five",
            "contact london control one three four decimal one two five",
            "contact tower one two four decimal two two five"])

    makers = [heading, altitude, flight_level, speed, qnh, direct, ils, squawk, contact,
              heading, altitude, flight_level, speed]
    fixset = set(default_fixes())
    out = []
    while len(out) < n:
        segs = [("CALLSIGN", callsign())]
        used = set()
        for _ in range(rng.choice((1, 1, 2, 2, 3))):
            lab, text = rng.choice(makers)()
            fam = {"ALTITUDE": "L", "FLIGHT_LEVEL": "L"}.get(lab, lab)
            if fam in used:
                continue
            used.add(fam)
            segs.append((lab, text))
        if rng.random() < 0.15:                               # callsign at the end
            segs = segs[1:] + segs[:1]
        tokens, tags = [], []
        for lab, text in segs:
            if rng.random() < 0.1 and lab != "CALLSIGN":
                tokens.append("and")
                tags.append("O")
            t = normalise_tokens(text)
            tokens += t
            tags += [f"B-{lab}"] + [f"I-{lab}"] * (len(t) - 1)
        if normalise_tokens(" ".join(tokens)) != tokens:
            continue                                          # normaliser joins across segments
        # check the labels agree with what the grammar makes of each span
        from server.voice.ml_tagger import bio_to_spans
        ok = True
        for lab, a, b in bio_to_spans(tags):
            if lab == "CALLSIGN":
                continue
            got, corr, unparsed = build_instructions(build_clauses(tokens[a:b], fixset), fixset)
            if unparsed or len(got) != 1 or got[0].type.upper() != lab:
                ok = False
                break
        if ok:
            out.append({"id": f"syn{len(out):05d}", "rec_id": f"syn{len(out) % 50}",
                        "src": "synthetic", "tokens": tokens, "tags": tags})
    return out


def _golden_texts() -> set[str]:
    keys = set()
    for p in (GOLDEN_PARSE, GOLDEN_REAL):
        if p.exists():
            for line in p.read_text().splitlines():
                row = json.loads(line)
                if "text" in row:
                    keys.add(" ".join(normalise_tokens(row["text"])))
    return keys


def _bucket(key: str) -> int:
    return zlib.crc32(key.encode()) % 10


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    fixes = set(default_fixes())
    golden = _golden_texts()
    real, rejected, reasons = [], [], {}
    raw = OUT / "raw.jsonl"
    rows = [json.loads(x) for x in raw.read_text().splitlines()] if raw.exists() else []
    controller = [r for r in rows if r["role"] == "controller"]
    for r in controller:
        res = label_real(r["text"], fixes)
        if isinstance(res, str):
            reasons[res] = reasons.get(res, 0) + 1
            rejected.append({**r, "reason": res})
            continue
        tokens, tags = res
        if " ".join(tokens) in golden:
            reasons["in_golden"] = reasons.get("in_golden", 0) + 1
            continue
        real.append({"id": r["id"], "rec_id": r["rec_id"], "src": "real",
                     "tokens": tokens, "tags": tags})
    syn = [s for s in synthetic(N_SYNTHETIC) if " ".join(s["tokens"]) not in golden]
    labels = sorted({t[2:] for row in real + syn for t in row["tags"] if t != "O"})
    train, dev, dev_real = [], [], []
    for row in real:
        (dev if _bucket(row["rec_id"]) == 0 else train).append(row)
        if _bucket(row["rec_id"]) == 0:
            dev_real.append(row)
    for row in syn:
        (dev if _bucket(row["id"]) == 0 else train).append(row)

    def dump(name: str, rows_: list[dict]) -> None:
        with (OUT / name).open("w") as fh:
            for x in rows_:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
    dump("labelled.jsonl", real + syn)
    dump("rejected.jsonl", rejected)
    dump("train.jsonl", train)
    dump("dev.jsonl", dev)
    dump("dev_real.jsonl", dev_real)
    (OUT / "labels.json").write_text(json.dumps(labels, indent=1) + "\n")
    stats = {"real_raw": len(rows), "real_controller": len(controller), "real_kept": len(real),
             "synthetic": len(syn), "train": len(train), "dev": len(dev),
             "dev_real": len(dev_real), "rejected_reasons": reasons}
    (OUT / "weak_label_stats.json").write_text(json.dumps(stats, indent=1) + "\n")
    print(json.dumps(stats, indent=1))
    print("labels:", labels)
    for row in real[:5]:
        print("  ", " ".join(f"{w}/{t}" for w, t in zip(row["tokens"], row["tags"]))[:160])
    return 0


if __name__ == "__main__":
    sys.exit(main())
