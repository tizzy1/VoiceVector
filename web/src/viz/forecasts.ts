import type { ConflictForecast, StateMsg } from '../protocol';
import { FT_TO_M, type Vec3 } from './sceneData';

export const forecastKey = (pair: readonly string[]) => [...pair].sort().join(' / ');
export function forecastTime(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—';
  const n = Math.round(seconds);
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
}
export function forecastStatus(state: StateMsg | null): 'disconnected' | 'unavailable' | 'waiting' | 'stale' | 'ready' {
  if (!state) return 'disconnected';
  if (!state.forecasts_available && !state.forecasts?.length) return 'unavailable';
  const computed = state.forecasts_computed_t ?? state.forecasts?.[0]?.computed_t;
  if (computed == null) return 'waiting';
  return !Number.isFinite(computed) || computed > state.t || state.t - computed > 4 ? 'stale' : 'ready';
}
export function visibleForecasts(state: StateMsg | null): ConflictForecast[] {
  if (forecastStatus(state) !== 'ready') return [];
  const seen = new Set<string>();
  return [...(state?.forecasts ?? [])].filter(f => f.pair.length === 2 && f.pair[0] !== f.pair[1]
    && Number.isFinite(f.p_conflict) && f.p_conflict >= .15 && f.p_conflict <= 1
    && Number.isFinite(f.min_sep_nm) && Number.isFinite(f.min_sep_ft)
    && f.computed_t <= state!.t && state!.t - f.computed_t <= 4)
    .sort((a, b) => b.p_conflict - a.p_conflict || forecastKey(a.pair).localeCompare(forecastKey(b.pair)))
    .filter(f => { const key = forecastKey(f.pair); if (seen.has(key)) return false; seen.add(key); return true; }).slice(0, 5);
}
export function forecastHasLoss(forecast: ConflictForecast, state: StateMsg | null): boolean {
  return Boolean(state?.alerts.some(a => a.kind === 'STCA_LOS' && a.callsigns.length === 2
    && forecastKey(a.callsigns) === forecastKey(forecast.pair)));
}
export interface FanPath { callsign: string; path: Vec3[]; high: boolean; loss: boolean }
export interface ForecastMarker { point: Vec3; loss: boolean }
export function forecastGeometry(state: StateMsg | null): { paths: FanPath[]; markers: ForecastMarker[]; highRisk: Set<string> } {
  const paths: FanPath[] = [], markers: ForecastMarker[] = [], highRisk = new Set<string>();
  const unique = new Set<string>();
  for (const f of visibleForecasts(state)) {
    if (f.p_conflict >= .5) f.pair.forEach(cs => highRisk.add(cs));
    const loss = forecastHasLoss(f, state), groups: FanPath[][] = f.pair.map(cs => (f.samples ?? [])
      .filter(s => s.callsign === cs && s.points.length >= 2 && s.points.length <= 9
        && s.points.every(p => p.length === 3 && p.every(Number.isFinite) && Math.abs(p[0]) <= 90 && Math.abs(p[1]) <= 180))
      .slice(0, 8).map(sample => ({ callsign: cs, high: f.p_conflict >= .5, loss,
        path: sample.points.map(([lat, lon, ft]) => [lon, lat, ft * FT_TO_M] as Vec3) })));
    for (const group of groups) for (const p of group) {
      const key = `${p.callsign}:${JSON.stringify(p.path)}`;
      if (!unique.has(key)) { unique.add(key); paths.push(p); }
    }
    // Compare time-aligned samples, so crossing at different times is not marked as a meeting.
    let best = Infinity, point: Vec3 | undefined;
    for (const a of groups[0]) for (const b of groups[1]) for (let i = 0; i < Math.min(a.path.length, b.path.length); i++) {
      const p = a.path[i], q = b.path[i];
      const east = (p[0] - q[0]) * 60 * Math.cos((p[1] + q[1]) / 2 * Math.PI / 180);
      const north = (p[1] - q[1]) * 60;
      const distance = (east * east + north * north) / 9 + Math.pow((p[2] - q[2]) / (1000 * FT_TO_M), 2);
      if (distance < best) { best = distance; point = p.map((v, j) => (v + q[j]) / 2) as Vec3; }
    }
    if (point) markers.push({ point, loss });
  }
  return { paths, markers, highRisk };
}
export interface ForecastNotice { t: number; text: string }
export class ForecastCrossings {
  private high = new Set<string>();
  private last = -1;
  update(state: StateMsg | null): ForecastNotice[] {
    if (!state || state.t < this.last) { this.high.clear(); this.last = -1; }
    if (!state) return [];
    this.last = state.t;
    const next = new Set<string>(), notices: ForecastNotice[] = [];
    for (const f of visibleForecasts(state)) if (f.p_conflict >= .5) {
      const key = forecastKey(f.pair); next.add(key);
      if (!this.high.has(key)) notices.push({ t: state.t,
        text: `Forecast: ${f.pair[0]} and ${f.pair[1]} ${Math.round(f.p_conflict * 100)}% conflict risk${f.t_conflict_s == null ? '' : ` in ${forecastTime(f.t_conflict_s)}`}` });
    }
    this.high = next;
    return notices;
  }
}
