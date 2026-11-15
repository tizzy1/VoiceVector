"""'Hear what you missed' (15 Nov, Sanju): keep the audio of pilot readbacks that carried a planted
mistake, so the debrief can play the exact transmission through the same radio sound.

Only readbacks with a spoken error are kept (modes "consistent" and "readback_only"; an execution-only
error is read back correctly, so there is nothing to hear), at most MAX_CLIPS per session, in memory.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Optional

import numpy as np

MAX_CLIPS = 30
SPOKEN_MODES = ("consistent", "readback_only")


class ReplayStore:
    def __init__(self, max_clips: int = MAX_CLIPS):
        self.clips: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self.max_clips = max_clips

    def keep(self, resp: Any, pcm: bytes, sample_rate: int, audio_format: str, text: str) -> list[str]:
        """Store ``pcm`` under every spoken error id of ``resp``; returns the ids kept."""
        if audio_format != "pcm_s16le" or not pcm:
            return []
        kept = []
        for rec in getattr(resp, "errors", []) or []:
            if getattr(rec, "mode", None) not in SPOKEN_MODES:
                continue
            self.clips[rec.id] = {"pcm": bytes(pcm), "rate": int(sample_rate), "text": text,
                                  "callsign": rec.callsign, "intended": rec.intended,
                                  "said": rec.readback_value, "kind": rec.kind}
            self.clips.move_to_end(rec.id)
            kept.append(rec.id)
        while len(self.clips) > self.max_clips:
            self.clips.popitem(last=False)
        return kept

    def wav(self, error_id: str, radio: bool = True) -> Optional[bytes]:
        clip = self.clips.get(error_id)
        if clip is None:
            return None
        from server.voice.voice_design import pcm16_to_wav
        from server.voice.voice_design import radio as radio_fx
        pcm, rate = clip["pcm"], clip["rate"]
        if radio:
            x = np.frombuffer(pcm[: len(pcm) // 2 * 2], "<i2").astype(np.float64) / 32768
            pcm = (np.clip(radio_fx(x, rate), -1, 1) * 32767).astype("<i2").tobytes()
        return pcm16_to_wav(pcm, rate)
