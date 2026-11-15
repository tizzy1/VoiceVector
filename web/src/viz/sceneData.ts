import type { AircraftPublic, StateMsg } from "../protocol";
import type { Airspace } from "../radar/Radar";

export type Vec3 = [number, number, number];
export interface SceneAirspace extends Airspace {
  _meta: { origin: { lat: number; lon: number } };
  runways: Record<string, { threshold: { xy_nm: [number, number] }; elevation_ft: number }>;
}
export const FT_TO_M = .3048;
export function position(x: number, y: number, alt: number, data: SceneAirspace): Vec3 {
  const { lat, lon } = data._meta.origin;
  return [lon + x / (60 * Math.cos(lat * Math.PI / 180)), lat + y / 60, alt * FT_TO_M];
}
export const activeAircraft = (aircraft: readonly AircraftPublic[]) => aircraft.filter(
  ac => ac.phase !== "LANDED" && ac.phase !== "HANDED_OFF" && ac.phase !== "QUEUED");

interface TrailSample { t: number; x: number; y: number; alt: number }
export interface TrailSegment { start: Vec3; end: Vec3; color: [number, number, number, number] }
export class FlightHistory {
  private latest = -1;
  private samples = new Map<string, TrailSample[]>();
  clear() { this.latest = -1; this.samples.clear(); }
  update(state: StateMsg | null) {
    if (!state || state.t < this.latest) this.clear();
    if (!state) return;
    const keep = new Set<string>();
    for (const ac of activeAircraft(state.aircraft)) {
      keep.add(ac.callsign);
      const old = this.samples.get(ac.callsign) ?? [];
      const next = old.filter(sample => sample.t >= state.t - 180 && sample.t !== state.t);
      next.push({ t: state.t, x: ac.x, y: ac.y, alt: ac.alt });
      this.samples.set(ac.callsign, next.slice(-181));
    }
    for (const key of this.samples.keys()) if (!keep.has(key)) this.samples.delete(key);
    this.latest = state.t;
  }
  segments(state: StateMsg, data: SceneAirspace): TrailSegment[] {
    return activeAircraft(state.aircraft).flatMap(ac => {
      const samples = this.samples.get(ac.callsign) ?? [];
      const color = ac.flow === "ARR" ? [112, 178, 194] : [225, 172, 86];
      return samples.slice(1).map((sample, index) => ({
        start: position(samples[index].x, samples[index].y, samples[index].alt, data),
        end: position(sample.x, sample.y, sample.alt, data),
        color: [...color, Math.round(15 + 130 * Math.max(0, 1 - (state.t - sample.t) / 180))] as TrailSegment["color"],
      }));
    });
  }
}

/** Original box-and-wing mesh. Coordinates: east/right X, nose/north Y, up Z, metres. */
export function aircraftMesh() {
  const vertices: number[] = [], normals: number[] = [];
  function triangle(a: Vec3, b: Vec3, c: Vec3) {
    const u = b.map((v, i) => v - a[i]), v = c.map((n, i) => n - a[i]);
    const normal = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    const length = Math.hypot(...normal) || 1;
    vertices.push(...a, ...b, ...c);
    for (let i = 0; i < 3; i++) normals.push(...normal.map(n => n / length));
  }
  function extrude(points: [number, number][], low: number, high: number) {
    for (let i = 1; i < points.length - 1; i++) {
      triangle([...points[0], high], [...points[i], high], [...points[i + 1], high]);
      triangle([...points[0], low], [...points[i + 1], low], [...points[i], low]);
    }
    for (let i = 0; i < points.length; i++) {
      const a = points[i], b = points[(i + 1) % points.length];
      triangle([...a, low], [...b, low], [...b, high]);
      triangle([...a, low], [...b, high], [...a, high]);
    }
  }
  const box = (x: number, y: number, w: number, l: number, low: number, high: number) =>
    extrude([[x - w / 2, y - l / 2], [x + w / 2, y - l / 2], [x + w / 2, y + l / 2], [x - w / 2, y + l / 2]], low, high);
  extrude([[-2, -19], [2, -19], [2.6, 12], [0, 22], [-2.6, 12]], -2, 2);
  for (const side of [-1, 1]) {
    extrude([[side * 2, 6], [side * 19, -4], [side * 19, -7], [side * 2, -1]], -.6, .6);
    extrude([[side * 1, -12], [side * 8, -17], [side * 8, -19], [side * 1, -17]], 0, 1);
    box(side * 6, -1, 2.5, 6, -4, -1);
  }
  box(0, -15, .8, 6, 1, 8);
  return { attributes: { POSITION: { value: new Float32Array(vertices), size: 3 }, NORMAL: { value: new Float32Array(normals), size: 3 } } };
}

export function modelMatrix(heading: number, scale: number): number[] {
  const radians = heading * Math.PI / 180, c = Math.cos(radians) * scale, s = Math.sin(radians) * scale;
  return [c, -s, 0, 0, s, c, 0, 0, 0, 0, scale, 0, 0, 0, 0, 1];
}

export function wrapSpeech(text: string, width = 42): string {
  const lines: string[] = [];
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const last = lines.length - 1;
    if (last >= 0 && lines[last].length + word.length + 1 <= width) lines[last] += ` ${word}`;
    else lines.push(word);
  }
  return lines.join("\n");
}
