import type { AircraftPublic } from "../protocol";
import { FT_TO_M, position, type SceneAirspace } from "./sceneData";

export type CameraMode = "overview" | "lock" | "chase" | "radio" | "departure" | "free";
export interface CameraPose { longitude: number; latitude: number; zoom: number; pitch: number; bearing: number; position: number[] }
export const initialPose: CameraPose = { longitude: -.171944, latitude: 51.150672, zoom: 9.7, pitch: 55, bearing: -25, position: [0, 0, 0] };
export function overviewPose(aircraft: readonly AircraftPublic[], data: SceneAirspace, width: number, height: number): CameraPose {
  const xs = [0, ...aircraft.map(ac => ac.x)], ys = [0, ...aircraft.map(ac => ac.y)];
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const [longitude, latitude] = position((minX + maxX) / 2, (minY + maxY) / 2, 0, data);
  const span = Math.max(24, maxX - minX, maxY - minY) * 1852 * 1.5;
  const pixels = Math.max(300, Math.min(width * .65, height));
  const zoom = Math.max(7.5, Math.min(11.5, Math.log2(156543.03392 * Math.cos(latitude * Math.PI / 180) * pixels / span)));
  return { ...initialPose, longitude, latitude, zoom };
}
export function aircraftPose(ac: AircraftPublic, data: SceneAirspace, chase = false): CameraPose {
  const [longitude, latitude] = position(ac.x, ac.y, ac.alt, data);
  return { longitude, latitude, zoom: chase ? 13.4 : 12.3, pitch: chase ? 70 : 58,
    bearing: chase ? ac.track : -25, position: [0, 0, Math.max(0, ac.alt * FT_TO_M)] };
}
const mix = (a: number, b: number, f: number) => a + (b - a) * f;
function blend(a: CameraPose, b: CameraPose, f: number): CameraPose {
  return { longitude: mix(a.longitude, b.longitude, f), latitude: mix(a.latitude, b.latitude, f),
    zoom: mix(a.zoom, b.zoom, f), pitch: mix(a.pitch, b.pitch, f),
    bearing: a.bearing + ((((b.bearing - a.bearing + 180) % 360 + 360) % 360) - 180) * f,
    position: a.position.map((v, i) => mix(v, b.position[i], f)) };
}
export class CameraDirector {
  pose = { ...initialPose };
  private key = "";
  private from = this.pose;
  private started = 0;
  private last = 0;
  private speaker = "";
  private speakerAt = -Infinity;
  manual(pose: CameraPose) { this.pose = pose; this.key = "free"; }
  radioTarget(speakers: readonly string[], aircraft: readonly AircraftPublic[], now: number): string {
    if (!aircraft.some(ac => ac.callsign === this.speaker)) this.speaker = "";
    const next = speakers.find(cs => aircraft.some(ac => ac.callsign === cs));
    if (next && next !== this.speaker && (!this.speaker || now - this.speakerAt >= 6000)) {
      this.speaker = next; this.speakerAt = now;
    }
    return this.speaker;
  }
  step(goal: CameraPose, key: string, now: number, reduced: boolean): CameraPose {
    if (key !== this.key) { this.key = key; this.from = this.pose; this.started = now; }
    const elapsed = Math.max(0, now - this.started), dt = Math.min(100, Math.max(0, now - this.last));
    this.last = now;
    if (reduced) this.pose = goal;
    else if (elapsed < 1800) {
      const f = elapsed / 1800;
      this.pose = blend(this.from, goal, f * f * (3 - 2 * f));
    } else this.pose = blend(this.pose, goal, 1 - Math.exp(-dt / 180));
    return this.pose;
  }
}
