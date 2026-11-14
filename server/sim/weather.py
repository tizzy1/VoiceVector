"""Session weather for the controller scope's METAR panel (SIM_SPEC §5.0).

The sim flies one constant wind and QNH per session (SIM_SPEC §1.3). The METAR is built
from exactly those values so the panel never disagrees with how aircraft fly. It is a
SIMULATED report, never a live one: `source` is always "SIMULATED" and the scope must
label it that way. Visibility, cloud and temperature come from the scenario's
`_meta.weather` block (or calm, CAVOK-like defaults) and do not affect the flight model.
"""
from __future__ import annotations

from typing import Any

DEFAULT = {"visibility_m": 9999, "cloud": [], "temp_c": 12, "dewpoint_c": 7, "obs_hhmm": "1750"}


def _t(c: int) -> str:
    return f"M{abs(c):02d}" if c < 0 else f"{c:02d}"


def build(icao: str, wind_from: float, wind_kt: float, qnh: int,
          extra: dict[str, Any] | None) -> dict[str, Any]:
    w = {**DEFAULT, **(extra or {})}
    hhmm = str(w["obs_hhmm"]).zfill(4)
    vis = int(w["visibility_m"])
    cloud = [{"cover": c["cover"], "base_ft": int(c["base_ft"])} for c in w["cloud"]]
    calm = round(wind_kt) == 0
    wind_dir = 0 if calm else int(round(wind_from / 10.0) * 10) % 360 or 360
    wind_txt = "00000KT" if calm else f"{wind_dir:03d}{round(wind_kt):02d}KT"
    if vis >= 9999 and not cloud:
        sky = "CAVOK"
    else:
        sky = (f"{min(vis, 9999):04d} "
               + (" ".join(f"{c['cover']}{c['base_ft'] // 100:03d}" for c in cloud) or "NSC"))
    raw = (f"{icao} 14{hhmm}Z {wind_txt} {sky} "
           f"{_t(int(w['temp_c']))}/{_t(int(w['dewpoint_c']))} Q{int(qnh):04d}")
    return {
        "source": "SIMULATED", "icao": icao, "observed": f"{hhmm[:2]}:{hhmm[2:]}Z",
        "wind_dir_deg": None if calm else wind_dir, "wind_kt": round(wind_kt), "calm": calm,
        "visibility_m": vis, "cloud": cloud, "cavok": sky == "CAVOK",
        "temp_c": int(w["temp_c"]), "dewpoint_c": int(w["dewpoint_c"]), "qnh_hpa": int(qnh),
        "raw": raw,
    }
