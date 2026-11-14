import type { AudioEngineExt } from "../audio/index";
import type { AudioActivity, ClientMsg, Role, ServerMsg } from "../protocol";

export interface RadioStatus { enabled: boolean; micReady: boolean; keyed: boolean; error: string }
interface Transport {
  send: (message: ClientMsg) => boolean;
  sendBinary: (frame: ArrayBuffer) => boolean;
}

export class RadioLink {
  private status: RadioStatus = { enabled: false, micReady: false, keyed: false, error: "" };
  private disposed = false;
  private unsubscribe: () => void;

  constructor(
    private engine: AudioEngineExt, private transport: Transport, private role: Role,
    private changed: (status: RadioStatus) => void,
    activity: (event: AudioActivity) => void,
  ) {
    this.unsubscribe = engine.onActivity(activity);
  }

  private update(changes: Partial<RadioStatus>) {
    this.status = { ...this.status, ...changes };
    if (!this.disposed) this.changed(this.status);
  }

  async enable(playback = true) {
    try {
      const initialized = this.engine.init({
        role: this.role, playback: true,
        sendJson: message => { this.transport.send(message); },
        sendBinary: frame => { this.transport.sendBinary(frame); },
      });
      this.engine.setPlayback(playback);
      await Promise.all([initialized, this.engine.resume()]);
      if (this.disposed) return;
      this.update({ enabled: true });
      await this.engine.whenMicReady();
      if (this.disposed) { this.engine.dispose(); return; }
      const error = this.engine.micError() ?? "";
      this.update({ micReady: this.role === "radar" && !error, error });
    } catch (error) {
      this.update({ error: error instanceof Error ? error.message : String(error) });
    }
  }

  down() {
    if (this.disposed || !this.status.micReady || this.status.keyed) return;
    this.engine.pttDown();
    this.update({ keyed: true });
  }

  up() {
    if (!this.status.keyed) return;
    this.engine.pttUp();
    this.update({ keyed: false });
  }

  handle(message: ServerMsg) { if (!this.disposed) this.engine.handleServerMsg(message); }
  playback(on: boolean) { this.engine.setPlayback(on); }
  volume(value: number) { this.engine.setVolume(value); }
  level() { return this.disposed ? 0 : this.engine.micLevel(); }
  clock(txId: string) { return this.disposed ? null : this.engine.pilotWordClock(txId); }

  dispose() {
    this.up();
    this.disposed = true;
    this.unsubscribe();
    this.engine.dispose();
    // A permission prompt can finish after the view disconnects.
    void this.engine.whenMicReady().then(() => this.engine.dispose());
  }
}

export function isTyping(target: EventTarget | null) {
  return target instanceof HTMLElement
    && (target.isContentEditable || Boolean(target.closest("input, textarea, select, button")));
}
