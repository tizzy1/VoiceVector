import { useEffect, useState } from 'react';
import type { ConflictForecast, StateMsg } from '../protocol';
import { forecastHasLoss, forecastKey, forecastStatus, forecastTime, visibleForecasts } from './forecasts';
import './forecast.css';
interface Row { forecast: ConflictForecast; leaving: boolean }
export function ForecastPanel({ state }: { state: StateMsg | null }) {
  const [rows, setRows] = useState<Row[]>(() => visibleForecasts(state).map(forecast => ({ forecast, leaving: false })));
  const status = forecastStatus(state);
  useEffect(() => {
    const fresh = visibleForecasts(state), keys = new Set(fresh.map(f => forecastKey(f.pair)));
    setRows(previous => [...fresh.map(forecast => ({ forecast, leaving: false })),
      ...previous.filter(r => !r.leaving && !keys.has(forecastKey(r.forecast.pair))).map(r => ({ ...r, leaving: true }))].slice(0, 5));
    const timer = setTimeout(() => setRows(previous => previous.filter(r => !r.leaving)), 180);
    return () => clearTimeout(timer);
  }, [state]);
  const messages = {
    disconnected: 'Waiting for a simulation connection.', unavailable: 'Forecast engine is unavailable.',
    waiting: 'Waiting for the first forecast.', stale: 'Forecast data is stale. Waiting for an update.',
    ready: 'No conflicts forecast in the next 2 minutes',
  };
  return <section id="conflict-forecast" className="forecast-panel" aria-label="Conflict forecast">
    <header><h2>CONFLICT FORECAST</h2><span>ADVISORY</span></header>
    <p className="forecast-subtitle">64 simulated futures per aircraft · next 2 min · updated every 2 s</p>
    {status === 'ready' && rows.length ? <ul>{rows.map(({ forecast: f, leaving }) => {
      const loss = forecastHasLoss(f, state);
      return <li key={forecastKey(f.pair)} className={`${loss ? 'forecast-loss' : f.p_conflict >= .5 ? 'forecast-high' : 'forecast-low'}${leaving ? ' forecast-leaving' : ''}`}>
        <div className="forecast-row-heading"><b>{f.pair[0]} ↔ {f.pair[1]}</b><strong>{Math.round(f.p_conflict * 100)}%</strong></div>
        <p className="forecast-names">{f.pair.map(cs => state?.aircraft.find(a => a.callsign === cs)?.spoken_plain ?? cs).join(' ↔ ')}</p>
        <meter aria-label={`Conflict probability ${f.pair.join(' and ')}`} min="0" max="1" value={f.p_conflict} />
        {loss && <p className="forecast-loss-label">LOSS OF SEPARATION</p>}
        <p>In {forecastTime(f.t_conflict_s)} · closest {f.min_sep_nm.toFixed(1)} NM / {Math.round(f.min_sep_ft).toLocaleString('en-GB')} ft</p>
        {f.samples.length === 0 && !leaving && <small>3D samples not supplied by simulator</small>}
      </li>;
    })}</ul> : <p className="forecast-empty" role="status">{messages[status]}</p>}
    <footer>Forecast, not an alert. The controller does not see this.</footer>
  </section>;
}
