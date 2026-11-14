import type { AircraftPublic, StateMsg } from "../protocol";

interface Sample { t: number; x: number; y: number }
export interface Track extends AircraftPublic { history: Sample[] }

function lerp(a: number, b: number, fraction: number) {
  return a + (b - a) * fraction;
}

export function angleBetween(a: number, b: number, fraction: number) {
  const delta = ((b - a + 540) % 360) - 180;
  return (a + delta * fraction + 360) % 360;
}

/** One simulation tick of display delay, bounded by authoritative server samples. */
export class MotionBuffer {
  private previous: StateMsg | null = null;
  private latest: StateMsg | null = null;
  private receivedAt = 0;
  private displayedAt = 0;
  private history = new Map<string, Sample[]>();

  update(state: StateMsg | null, now: number) {
    if (!state || (this.latest && state.t < this.latest.t)) this.clear();
    if (!state) return;
    const old = this.latest;
    if (!old || state.t > old.t) {
      this.previous = old ?? state;
      const history = new Map<string, Sample[]>();
      for (const ac of state.aircraft) {
        const samples = this.history.get(ac.callsign) ?? [];
        history.set(ac.callsign, [...samples, { t: state.t, x: ac.x, y: ac.y }].slice(-6));
      }
      this.history = history;
    }
    if (!old || state.t !== old.t || state.paused !== old.paused
      || state.sim_speed !== old.sim_speed) this.receivedAt = now;
    this.latest = state;
    if (!old || state.paused) this.displayedAt = state.t;
  }

  clear() {
    this.latest = this.previous = null;
    this.history.clear();
    this.displayedAt = 0;
  }

  sample(now: number): Track[] {
    const latest = this.latest;
    if (!latest) return [];
    const previous = this.previous ?? latest;
    const elapsed = Math.max(0, now - this.receivedAt) / 1000;
    const target = latest.paused ? latest.t : latest.t - 1 + elapsed * latest.sim_speed;
    this.displayedAt = Math.min(latest.t,
      Math.max(this.displayedAt, previous.t, target));
    const fraction = latest.t === previous.t ? 1
      : (this.displayedAt - previous.t) / (latest.t - previous.t);
    const prior = new Map(previous.aircraft.map(ac => [ac.callsign, ac]));
    return latest.aircraft.map(ac => {
      const before = prior.get(ac.callsign) ?? ac;
      return {
        ...ac,
        x: lerp(before.x, ac.x, fraction),
        y: lerp(before.y, ac.y, fraction),
        alt: lerp(before.alt, ac.alt, fraction),
        hdg: angleBetween(before.hdg, ac.hdg, fraction),
        track: angleBetween(before.track, ac.track, fraction),
        history: (this.history.get(ac.callsign) ?? [])
          .filter(p => p.t < this.displayedAt).slice(-5),
      };
    });
  }
}

export function vectorEnd(ac: Pick<AircraftPublic, "x" | "y" | "track" | "gs">,
  minutes: number): [number, number] {
  const distance = ac.gs * minutes / 60;
  const radians = ac.track * Math.PI / 180;
  return [ac.x + Math.sin(radians) * distance, ac.y + Math.cos(radians) * distance];
}
