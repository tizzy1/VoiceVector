import type { AircraftPublic } from "../protocol";

export function groupTraffic(aircraft: readonly AircraftPublic[]) {
  const active = aircraft.filter(ac => ac.phase !== "LANDED" && ac.phase !== "HANDED_OFF");
  return {
    arrivals: active.filter(ac => ac.flow === "ARR"),
    queued: active.filter(ac => ac.flow === "DEP" && ac.phase === "QUEUED"),
    departing: active.filter(ac => ac.flow === "DEP" && ac.phase === "TAKEOFF"),
    airborne: active.filter(ac => ac.flow === "DEP"
      && ac.phase !== "QUEUED" && ac.phase !== "TAKEOFF"),
  };
}
