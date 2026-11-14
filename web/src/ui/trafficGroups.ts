import type { AircraftPublic } from "../protocol";

export type TrafficSort = "session" | "callsign-asc" | "callsign-desc" | "altitude-asc" | "altitude-desc";

export function matchesCallsign(callsign: string, query: string): boolean {
  return callsign.toUpperCase().includes(query.trim().toUpperCase());
}

export function groupTraffic(aircraft: readonly AircraftPublic[], query = "", sort: TrafficSort = "session") {
  const active = aircraft.filter(ac => ac.phase !== "LANDED" && ac.phase !== "HANDED_OFF"
    && matchesCallsign(ac.callsign, query));
  if (sort !== "session") active.sort((a, b) => {
    const callsign = a.callsign.localeCompare(b.callsign, "en", { numeric: true });
    if (sort === "callsign-asc") return callsign;
    if (sort === "callsign-desc") return -callsign;
    const altitude = sort === "altitude-asc" ? a.alt - b.alt : b.alt - a.alt;
    return altitude || callsign;
  });
  return {
    arrivals: active.filter(ac => ac.flow === "ARR"),
    queued: active.filter(ac => ac.flow === "DEP" && ac.phase === "QUEUED"),
    departing: active.filter(ac => ac.flow === "DEP" && ac.phase === "TAKEOFF"),
    airborne: active.filter(ac => ac.flow === "DEP"
      && ac.phase !== "QUEUED" && ac.phase !== "TAKEOFF"),
  };
}
