"""Judge challenge (15 Nov, Sanju): land BAW117 from the TIMBA hold as fast and as cleanly as you can.

Active only in the `judge` scenario. Listens to the event bus and the phraseology coach:
  * clock: starts at the judge's first transmission, stops when the aircraft lands (sim seconds);
  * score 0-100 = time (40) + phraseology (40, coach average) + safety (20, minus go-arounds and
    conflict alerts) + 5 per pilot readback error caught, - 5 per error missed;
  * leaderboard: top 10, kept in runs/leaderboard.json (survives restarts); the judge may type a name.
The numbers shown are the simulator's own (sim time, its landing / go-around / alert events).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from server.contracts import ChallengeEntry, ChallengeMsg, Event

logger = logging.getLogger(__name__)

TARGET_CALLSIGN = "BAW117"
# A clean run from TIMBA (base leg, intercept beyond 9 NM) lands in ~11 sim minutes (live check:
# 675 s), so full time points up to 9 min is reachable only with tight vectors; 10 of 40 at 25 min.
FAST_S, SLOW_S = 540.0, 1500.0
TIME_POINTS, PHRASE_POINTS, SAFETY_POINTS = 40, 40, 20
DEFAULT_PHRASE = 75.0                   # no coached calls yet (typed-only before the coach lands)
TOP_N = 10
MAX_NAME = 24


def time_points(seconds: float) -> float:
    if seconds <= FAST_S:
        return float(TIME_POINTS)
    if seconds >= SLOW_S:
        return 10.0
    return TIME_POINTS - (TIME_POINTS - 10.0) * (seconds - FAST_S) / (SLOW_S - FAST_S)


class Challenge:
    def __init__(self, board_path: Path):
        self.board_path = Path(board_path)
        self.board: list[dict[str, Any]] = self._load()
        self.reset(active=False)

    # ------------------------------------------------------------------ state
    def reset(self, active: bool) -> None:
        self.active = active
        self.status = "waiting" if active else "off"
        self.t_start: Optional[float] = None
        self.t_land: Optional[float] = None
        self.now = 0.0
        self.calls = 0
        self.coach_scores: list[int] = []
        self.go_arounds = self.alerts = self.caught = self.missed = 0
        self.entry_id: Optional[str] = None

    def start(self, scenario: str) -> None:
        self.reset(active=scenario == "judge")

    # ------------------------------------------------------------------ inputs
    def on_event(self, e: Event) -> None:
        if not self.active:
            return
        try:
            self.now = max(self.now, float(e.t))
            if self.status == "landed":
                return
            if e.type == "ctrl_transmission":
                self.calls += 1
                if self.t_start is None:
                    self.t_start, self.status = float(e.t), "running"
            elif e.type == "go_around" and e.callsign == TARGET_CALLSIGN:
                self.go_arounds += 1
            elif e.type == "alert_start" and str(e.data.get("kind", "")).startswith("STCA"):
                self.alerts += 1
            elif e.type == "error_caught":
                self.caught += 1
            elif e.type == "error_missed":
                self.missed += 1
            elif e.type == "landing" and e.callsign == TARGET_CALLSIGN:
                self.t_land = float(e.t)
                if self.t_start is None:
                    self.t_start = 0.0
                self.status = "landed"
                self._record()
        except Exception:                                   # noqa: BLE001  (never break the session)
            logger.exception("challenge event failed")

    def on_coach(self, score: int) -> None:
        if self.active and self.status != "landed":
            self.coach_scores.append(int(score))

    def tick(self, t: float) -> None:
        self.now = max(self.now, float(t))

    # ------------------------------------------------------------------ scoring
    def elapsed(self) -> float:
        if self.t_start is None:
            return 0.0
        end = self.t_land if self.t_land is not None else self.now
        return max(0.0, end - self.t_start)

    def phraseology(self) -> float:
        return sum(self.coach_scores) / len(self.coach_scores) if self.coach_scores else DEFAULT_PHRASE

    def score(self) -> int:
        safety = max(0, SAFETY_POINTS - 10 * self.go_arounds - 5 * self.alerts)
        total = time_points(self.elapsed()) + PHRASE_POINTS * self.phraseology() / 100 + safety
        total += 5 * self.caught - 5 * self.missed
        return int(round(max(0, min(100, total))))

    # ------------------------------------------------------------------ leaderboard
    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.board_path.read_text())
            return [d for d in data if isinstance(d, dict) and "score" in d][:TOP_N]
        except (OSError, ValueError):
            return []

    def _save(self) -> None:
        try:
            self.board_path.parent.mkdir(parents=True, exist_ok=True)
            self.board_path.write_text(json.dumps(self.board, indent=1))
        except OSError:
            logger.exception("leaderboard not saved")

    def _record(self) -> None:
        self.entry_id = f"{int(time.time() * 1000)}"
        entry = {"id": self.entry_id, "name": f"Judge {len(self.board) + 1}", "score": self.score(),
                 "time_s": round(self.elapsed()), "phraseology": round(self.phraseology()),
                 "go_arounds": self.go_arounds, "calls": self.calls, "at": time.strftime("%H:%M")}
        self.board = sorted(self.board + [entry], key=lambda d: (-d["score"], d["time_s"]))[:TOP_N]
        self._save()

    def set_name(self, name: str) -> bool:
        name = " ".join(str(name).split())[:MAX_NAME]
        if not name or self.entry_id is None:
            return False
        for d in self.board:
            if d.get("id") == self.entry_id:
                d["name"] = name
                self._save()
                return True
        return False

    def rank(self) -> Optional[int]:
        return next((i + 1 for i, d in enumerate(self.board) if d.get("id") == self.entry_id), None)

    # ------------------------------------------------------------------ message
    def message(self) -> ChallengeMsg:
        return ChallengeMsg(
            active=self.active, status=self.status, elapsed_s=round(self.elapsed(), 1), calls=self.calls,
            phraseology=round(self.phraseology()) if self.coach_scores else None,
            go_arounds=self.go_arounds, alerts=self.alerts, caught=self.caught, missed=self.missed,
            score=self.score() if self.status != "waiting" else None, rank=self.rank(),
            entry_id=self.entry_id,
            leaderboard=[ChallengeEntry(**{k: d.get(k) for k in ("id", "name", "score", "time_s",
                                                                  "phraseology", "go_arounds", "calls", "at")})
                         for d in self.board],
        )
