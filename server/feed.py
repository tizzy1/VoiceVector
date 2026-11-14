"""Human-readable event feed shared by both views."""
from server.contracts import Event

LABELS = {
    "session_start": "Session started", "session_pause": "Session paused",
    "session_resume": "Session resumed", "session_end": "Session ended",
    "ctrl_blocked": "Controller transmission blocked",
    "error_caught": "Readback error caught", "error_missed": "Pilot error missed",
    "landing": "Landed", "takeoff": "Airborne", "overlap": "Frequency blocked",
    "instruction_applied": "Instruction applied",
}


def feed_text(event: Event) -> str:
    label = str(event.data.get("title") or LABELS.get(event.type, event.type.replace("_", " ")))
    return f"{event.callsign} · {label}" if event.callsign else label
