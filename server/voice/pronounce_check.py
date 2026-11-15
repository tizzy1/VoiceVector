"""Pronunciation check (VOICE_PIPELINE §11.1): hear every letter, navaid, fix and RNAV point
through the real game voice and approve or reject each one by ear.

    .venv/bin/python -m server.voice.pronounce_check [--voice uk-captain-brisk] [--radio]
    open runs/pronunciation/index.html

Each clip is exactly what the game sends to ElevenLabs Flash v2.5 (composer speech + approved
respellings). For phonetic letters the plain ICAO word and every CANDIDATE respelling are played
too, so a better spelling can be picked. Clips are cached in runs/pronunciation/ (gitignored), so
re-runs only synthesise what changed. "Export" on the page gives JSON to send back: approved
candidates go into ``pronounce.RESPELL``; fixes that need a respelling go into
``data/spoken_names.json`` (Tudor).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "runs" / "pronunciation"


def items(composer) -> list[dict]:
    """Every thing to check: section, label, the text the TTS gets, and whether it is live in game."""
    from server.voice.parser import default_fixes
    from server.voice.pronounce import CANDIDATES, ICAO_WORD, RESPELL, is_spelled_point, respell, spell
    out: list[dict] = []
    for letter, word in ICAO_WORD.items():
        live = respell(word)
        out.append({"section": "Phonetic letters", "id": f"letter:{letter}", "label": f"{letter} — {word}",
                    "text": f"{live.capitalize()}. Information {live}.", "live": True,
                    "note": "respelled" if word in RESPELL else ""})
        if word in RESPELL:
            out.append({"section": "Phonetic letters", "id": f"letter:{letter}:plain", "label": f"{letter} — {word}",
                        "text": f"{word.capitalize()}. Information {word}.", "live": False, "note": "plain spelling (not used)"})
        for alt in CANDIDATES.get(word, []):
            out.append({"section": "Phonetic letters", "id": f"letter:{letter}:{alt}", "label": f"{letter} — {word}",
                        "text": f"{alt.capitalize()}. Information {alt}.", "live": False, "note": f"candidate “{alt}”"})
    for ident in sorted(composer.navaid_names):
        name = composer.navaid_names[ident]
        out.append({"section": "Navaids (station names)", "id": f"navaid:{ident}", "label": f"{ident} — {name}",
                    "text": respell(f"Proceeding direct {name.lower()}, {name.lower()} one x-ray arrival."),
                    "live": True, "note": ""})
    fixes = sorted(default_fixes())
    for ident in fixes:
        if ident in composer.navaid_names or is_spelled_point(ident) or not ident.isalpha():
            continue
        speech = composer.fix_speech(ident)[1]
        out.append({"section": "Five-letter fixes", "id": f"fix:{ident}", "label": ident,
                    "text": respell(f"Direct {speech}, then hold at {speech}."), "live": True, "note": ""})
    for ident in fixes:
        if is_spelled_point(ident):
            out.append({"section": "RNAV / DME points (spelled)", "id": f"point:{ident}", "label": ident,
                        "text": respell(f"Direct {spell(ident)}."), "live": True, "note": ""})
    return out


async def render(items_: list[dict], voice_id: str, settings: dict, radio: bool) -> None:
    from server.voice.tts import ElevenLabsTTS
    from server.voice.voice_design import pcm16_to_wav
    from server.voice.voice_design import radio as radio_fx
    tts = ElevenLabsTTS(os.environ["ELEVENLABS_API_KEY"], timeout=20)
    sem = asyncio.Semaphore(4)

    async def one(item: dict) -> None:
        key = hashlib.sha1(json.dumps([item["text"], voice_id, settings, radio]).encode()).hexdigest()[:12]
        path = OUT / f"{item['id'].replace(':', '_')}_{key}.wav"
        item["file"] = path.name
        if path.exists():
            return
        async with sem:
            pcm = b"".join([c async for c in tts.stream(item["text"], voice_id, settings)])
        if radio:
            x = np.frombuffer(pcm[: len(pcm) // 2 * 2], "<i2").astype(np.float64) / 32768
            pcm = (np.clip(radio_fx(x, tts.sample_rate), -1, 1) * 32767).astype("<i2").tobytes()
        path.write_bytes(pcm16_to_wav(pcm, tts.sample_rate))

    try:
        await asyncio.gather(*(one(i) for i in items_))
    finally:
        await tts.aclose()


def page(items_: list[dict], voice_name: str, radio: bool) -> str:
    rows, section = [], None
    for i, it in enumerate(items_):
        if it["section"] != section:
            section = it["section"]
            rows.append(f'<tr class="sec"><th colspan="4">{html.escape(section)}</th></tr>')
        tag = '<span class="live">in game</span>' if it["live"] else '<span class="alt">alternative</span>'
        rows.append(
            f'<tr data-id="{html.escape(it["id"])}"><td><b>{html.escape(it["label"])}</b> {tag}'
            f'<div class="note">{html.escape(it["note"])}</div><div class="said">“{html.escape(it["text"])}”</div></td>'
            f'<td><audio controls preload="none" src="{html.escape(it["file"])}"></audio></td>'
            f'<td class="vote"><label><input type="radio" name="v{i}" value="ok"> OK</label>'
            f'<label><input type="radio" name="v{i}" value="wrong"> Wrong</label></td>'
            f'<td><input class="cmt" placeholder="how should it sound?"></td></tr>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Pronunciation Check</title>
<style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e1dc;--acc:#2f6f4f;--alt:#8a5a00}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161615;--fg:#ecebe6;--mut:#9c9b95;--line:#2c2c2a;--acc:#7cc4a0;--alt:#e0b060}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0;padding:24px 16px}}
main{{max-width:1000px;margin:auto}} h1{{font-size:22px;margin:0 0 4px}} p{{color:var(--mut);margin:0 0 16px}}
table{{width:100%;border-collapse:collapse}} td,th{{padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:middle;text-align:left}}
tr.sec th{{padding-top:22px;font-size:17px}} .note,.said{{color:var(--mut);font-size:13px}}
.live,.alt{{font-size:11px;border:1px solid;border-radius:9px;padding:0 6px;margin-left:4px}} .live{{color:var(--acc)}} .alt{{color:var(--alt)}}
.vote label{{margin-right:10px;white-space:nowrap}} audio{{height:32px;max-width:260px}} .cmt{{width:100%;min-width:120px;background:transparent;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px}}
button{{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid var(--acc);background:var(--acc);color:var(--bg);cursor:pointer}}
textarea{{width:100%;height:160px;margin-top:10px;background:transparent;color:var(--fg);border:1px solid var(--line)}}
@media (max-width:700px){{td{{display:block;border:0}} tr{{display:block;border-bottom:1px solid var(--line)}}}}
</style></head><body><main>
<h1>Pronunciation check</h1>
<p>Voice: {html.escape(voice_name)} · {'through the radio' if radio else 'clean (no radio filter)'} · ElevenLabs Flash v2.5, exactly what the game sends.
Mark each clip OK or Wrong; for a letter, mark the best alternative OK. Then Export and send the JSON to Sanju.</p>
<table>{''.join(rows)}</table>
<p style="margin-top:20px"><button id="exp">Export</button></p><textarea id="out" readonly></textarea>
<script>
document.getElementById('exp').onclick=()=>{{const r=[];document.querySelectorAll('tr[data-id]').forEach(tr=>{{
const v=tr.querySelector('input[type=radio]:checked');const c=tr.querySelector('.cmt').value.trim();
if(v||c)r.push({{id:tr.dataset.id,vote:v?v.value:null,comment:c||null}})}});
const o=document.getElementById('out');o.value=JSON.stringify(r,null,1);o.select();
try{{navigator.clipboard.writeText(o.value)}}catch(e){{}}}};
</script></main></body></html>"""


async def main(argv: list[str]) -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--voice", default="uk-captain-brisk", help="slug from data/voices.json")
    ap.add_argument("--radio", action="store_true", help="play through the game's radio filter")
    args = ap.parse_args(argv)
    if not os.environ.get("ELEVENLABS_API_KEY"):
        print("ELEVENLABS_API_KEY missing")
        return 2
    from server.pilot.readback import Composer
    from server.voice.voices import Voices, settings_for
    entries = {e.get("slug"): e for e in json.loads((ROOT / "data" / "voices.json").read_text())["voices"]}
    if args.voice not in entries:
        print(f"unknown voice {args.voice!r}; one of: {', '.join(sorted(k for k in entries if k))}")
        return 2
    entry = entries[args.voice]
    voice = next(v for v in Voices.load(ROOT / "data").pool if v.id == entry["id"])
    persona = (entry.get("personas") or ["crisp_airline"])[0]
    composer = Composer(ROOT / "data")
    todo = items(composer)
    OUT.mkdir(parents=True, exist_ok=True)
    await render(todo, entry["id"], settings_for(persona, voice=voice), args.radio)
    index = OUT / "index.html"
    index.write_text(page(todo, entry.get("name", args.voice), args.radio), encoding="utf-8")
    print(f"{len(todo)} clips -> {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
