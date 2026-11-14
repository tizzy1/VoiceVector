import type { Metar } from "../protocol";
import "./weather.css";

const number = (value: number) => value.toLocaleString("en-GB");

export function WeatherPanel({ metar, connected }: { metar?: Metar | null; connected: boolean }) {
  // This interface currently accepts scenario weather only; never imply a live observation.
  const weather = connected && metar?.source === "SIMULATED" && metar.icao === "EGKK" ? metar : null;
  return <details className="weather-panel" open>
    <summary>EGKK weather <span className={weather ? "weather-source" : "muted"}>
      {weather ? "SIMULATED" : "UNAVAILABLE"}</span></summary>
    {weather ? <>
      <p className="weather-observed">Scenario observation · {weather.observed}</p>
      <dl>
        <div><dt>Wind</dt><dd>{weather.calm ? "Calm" :
          `${weather.wind_dir_deg == null ? "VRB" : String(weather.wind_dir_deg).padStart(3, "0") + "°"} / ${weather.wind_kt} kt`}</dd></div>
        <div><dt>Visibility</dt><dd>{weather.visibility_m >= 9999 ? "10 km or more" :
          `${number(weather.visibility_m)} m`}{weather.cavok ? " · CAVOK" : ""}</dd></div>
        <div><dt>Temperature / dew point</dt><dd>{weather.temp_c}°C / {weather.dewpoint_c}°C</dd></div>
        <div><dt>QNH</dt><dd>{weather.qnh_hpa} hPa</dd></div>
        <div className="weather-cloud"><dt>Cloud</dt><dd>{weather.cloud.length
          ? weather.cloud.map((layer, index) => <span key={index}>
            {layer.cover} · {number(layer.base_ft)} ft</span>) : "No significant cloud"}</dd></div>
      </dl>
      <details className="weather-raw"><summary>Raw simulated METAR</summary><code>{weather.raw}</code></details>
    </> : <p className="muted">{connected ? "No scenario weather supplied." : "Reconnect to receive scenario weather."}</p>}
  </details>;
}
