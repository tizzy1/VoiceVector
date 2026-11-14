// When does the radio squeal? Pure decision logic (no Web Audio), so it is unit-tested in Node.
//
// A transmission is "stepped on" when
//   * the server marked it ``blocked`` in audio_start, or
//   * an ``overlap`` event with ``with_ctrl`` listed it (the controller keyed over the pilot), or
//   * it shares an ``overlap_group`` with another stream that is on the air at the same time.
// While any such stream is on the air the heterodyne is engaged; it releases when the last ends.

export interface MixerStream {
  txId: string;
  blocked: boolean;
  group: string | null;
}

export type EngageFn = (on: boolean, seed: string) => void;

const PENDING_TTL_MS = 5000;

export class OverlapMixer {
  private streams = new Map<string, MixerStream>();
  private pendingBlocked = new Map<string, number>(); // overlap event arrived before audio_start
  private engaged = false;
  private events = 0;
  private readonly engage: EngageFn;
  private readonly now: () => number;

  constructor(engage: EngageFn, now: () => number = () => Date.now()) {
    this.engage = engage;
    this.now = now;
  }

  add(stream: MixerStream): void {
    const pending = this.pendingBlocked.get(stream.txId);
    const blocked = stream.blocked || (pending !== undefined && this.now() - pending < PENDING_TTL_MS);
    this.pendingBlocked.delete(stream.txId);
    this.streams.set(stream.txId, { ...stream, blocked });
    this.update();
  }

  remove(txId: string): void {
    this.streams.delete(txId);
    this.update();
  }

  /** ``overlap`` event from the server: those transmissions were stepped on. */
  overlap(txIds: string[], withCtrl: boolean): void {
    if (!withCtrl) return;
    const t = this.now();
    for (const id of txIds) {
      const s = this.streams.get(id);
      if (s) s.blocked = true;
      else this.pendingBlocked.set(id, t);
    }
    this.update();
  }

  /** Controller keyed over everything currently on the air (event lacked ids). */
  overlapAllActive(): void {
    for (const s of this.streams.values()) s.blocked = true;
    this.update();
  }

  get isEngaged(): boolean {
    return this.engaged;
  }

  private shouldEngage(): boolean {
    const groups = new Map<string, number>();
    for (const s of this.streams.values()) {
      if (s.blocked) return true;
      if (s.group) groups.set(s.group, (groups.get(s.group) ?? 0) + 1);
    }
    return [...groups.values()].some((n) => n >= 2);
  }

  private update(): void {
    const on = this.shouldEngage();
    if (on === this.engaged) return;
    this.engaged = on;
    if (on) this.events += 1;
    this.engage(on, `squeal-${this.events}`);
  }
}
