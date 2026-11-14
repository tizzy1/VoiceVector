// The radio: every pilot stream is summed into one bus and pushed through
//   (overdrive -> hard clip) -> high-pass 350 Hz -> presence +2 dB @1.4 kHz -> low-pass 2.6 kHz
//   -> high-shelf -6 dB @2.2 kHz -> tanh saturation -> volume   (fitted to real VHF audio, see dsp.ts)
// with a faint hiss while a carrier is up and a short band-passed noise burst (squelch tail) when
// it drops. The heterodyne squeal (~1.1 kHz, AM ~7 Hz) is an oscillator summed into the same bus,
// so a "stepped on" transmission is clipped together with it, exactly like a real over-modulated
// radio.
import { HETERODYNE, RADIO, hardClipCurve, heterodyneParams, noiseSamples, tanhCurve } from "./dsp.ts";

export class RadioBus {
  /** Connect each stream's gain node here. */
  readonly input: GainNode;
  private readonly ctx: AudioContext;
  private readonly overdrive: GainNode;
  private readonly master: GainNode;
  private readonly hiss: GainNode;
  private readonly oscOut: GainNode;
  private readonly carrier: OscillatorNode;
  private readonly lfo: OscillatorNode;
  private carriers = 0;
  private engaged = false;
  private volume = 1;
  private muted = false;
  private squelchSeed = 1;

  constructor(ctx: AudioContext, destination: AudioNode = ctx.destination) {
    this.ctx = ctx;
    this.input = ctx.createGain();
    this.overdrive = ctx.createGain();
    const clip = ctx.createWaveShaper();
    clip.curve = hardClipCurve();
    const highpass = ctx.createBiquadFilter();
    highpass.type = "highpass";
    highpass.frequency.value = RADIO.highpassHz;
    const presence = ctx.createBiquadFilter();
    presence.type = "peaking";
    presence.frequency.value = RADIO.presenceHz;
    presence.gain.value = RADIO.presenceDb;
    const lowpass = ctx.createBiquadFilter();
    lowpass.type = "lowpass";
    lowpass.frequency.value = RADIO.lowpassHz;
    const shelf = ctx.createBiquadFilter();
    shelf.type = "highshelf";
    shelf.frequency.value = RADIO.shelfHz;
    shelf.gain.value = RADIO.shelfDb;
    const saturate = ctx.createWaveShaper();
    saturate.curve = tanhCurve();
    this.master = ctx.createGain();
    this.input.connect(this.overdrive);
    this.overdrive.connect(clip);
    clip.connect(highpass);
    highpass.connect(presence);
    presence.connect(lowpass);
    lowpass.connect(shelf);
    shelf.connect(saturate);
    saturate.connect(this.master);
    this.master.connect(destination);

    // faint hiss under a transmission (looping band-passed noise, silent until a carrier is up)
    this.hiss = ctx.createGain();
    this.hiss.gain.value = 0;
    const noise = ctx.createBufferSource();
    noise.buffer = this.noiseBuffer(1.0, 7);
    noise.loop = true;
    const hissBand = ctx.createBiquadFilter();
    hissBand.type = "bandpass";
    hissBand.frequency.value = 1500;
    noise.connect(hissBand);
    hissBand.connect(this.hiss);
    this.hiss.connect(this.master);
    noise.start();

    // heterodyne: carrier oscillator, amplitude-modulated by a slow LFO, muted until engaged
    this.carrier = ctx.createOscillator();
    this.carrier.type = "sine";
    this.carrier.frequency.value = HETERODYNE.carrierHz;
    const am = ctx.createGain();
    am.gain.value = 0.5;
    this.lfo = ctx.createOscillator();
    this.lfo.type = "sine";
    this.lfo.frequency.value = HETERODYNE.lfoHz;
    const depth = ctx.createGain();
    depth.gain.value = 0.5;
    this.lfo.connect(depth);
    depth.connect(am.gain);
    this.oscOut = ctx.createGain();
    this.oscOut.gain.value = 0;
    this.carrier.connect(am);
    am.connect(this.oscOut);
    this.oscOut.connect(this.input);
    this.carrier.start();
    this.lfo.start();
  }

  private noiseBuffer(seconds: number, seed: number): AudioBuffer {
    const length = Math.max(1, Math.floor(this.ctx.sampleRate * seconds));
    const buffer = this.ctx.createBuffer(1, length, this.ctx.sampleRate);
    buffer.copyToChannel(noiseSamples(length, seed), 0);
    return buffer;
  }

  private ramp(param: AudioParam, value: number, seconds: number): void {
    const now = this.ctx.currentTime;
    param.cancelScheduledValues(now);
    param.setValueAtTime(param.value, now);
    param.linearRampToValueAtTime(value, now + seconds);
  }

  /** A pilot carrier came up / dropped. The hiss follows the first/last one; the last adds a squelch tail. */
  acquire(): void {
    this.carriers += 1;
    if (this.carriers === 1) this.ramp(this.hiss.gain, RADIO.hissLevel, 0.03);
  }

  release(at: number = this.ctx.currentTime): void {
    this.carriers = Math.max(0, this.carriers - 1);
    if (this.carriers > 0) return;
    this.ramp(this.hiss.gain, 0, 0.05);
    this.squelchTail(Math.max(at, this.ctx.currentTime));
  }

  /** ~150 ms band-passed noise burst that decays: the sound of the carrier dropping. */
  squelchTail(at: number): void {
    const seconds = RADIO.squelchMs / 1000;
    const source = this.ctx.createBufferSource();
    source.buffer = this.noiseBuffer(seconds, (this.squelchSeed += 1));
    const band = this.ctx.createBiquadFilter();
    band.type = "bandpass";
    band.frequency.value = RADIO.squelchBandHz;
    const env = this.ctx.createGain();
    env.gain.setValueAtTime(RADIO.squelchLevel, at);
    env.gain.linearRampToValueAtTime(0, at + seconds);
    source.connect(band);
    band.connect(env);
    env.connect(this.master);
    source.start(at);
  }

  /** Switch the heterodyne squeal + over-modulation on/off (each event gets its own pitch). */
  setEngaged(on: boolean, seed: string | number = 0): void {
    if (on === this.engaged) return;
    this.engaged = on;
    if (on) {
      const { carrierHz, lfoHz } = heterodyneParams(seed);
      const now = this.ctx.currentTime;
      this.carrier.frequency.setValueAtTime(carrierHz, now);
      this.lfo.frequency.setValueAtTime(lfoHz, now);
      this.ramp(this.oscOut.gain, HETERODYNE.oscLevel, 0.02);
      this.ramp(this.overdrive.gain, HETERODYNE.overdrive, 0.02);
    } else {
      this.ramp(this.oscOut.gain, 0, HETERODYNE.releaseMs / 1000);
      this.ramp(this.overdrive.gain, 1, HETERODYNE.releaseMs / 1000);
    }
  }

  get isEngaged(): boolean {
    return this.engaged;
  }

  get carrierCount(): number {
    return this.carriers;
  }

  setVolume(v: number): void {
    this.volume = Math.max(0, Math.min(1, v));
    this.apply();
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    this.apply();
  }

  private apply(): void {
    this.master.gain.value = this.muted ? 0 : this.volume;
  }
}
