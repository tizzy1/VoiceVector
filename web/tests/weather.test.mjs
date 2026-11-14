import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const source = await readFile(new URL("../src/ui/WeatherPanel.tsx", import.meta.url), "utf8");
let { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
} });
outputText = outputText.replace('import "./weather.css";', "")
  .replace('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime")));
const { WeatherPanel } = await import("data:text/javascript;base64," + Buffer.from(outputText).toString("base64"));
const metar = {
  source: "SIMULATED", icao: "EGKK", observed: "17:50Z", wind_dir_deg: 240, wind_kt: 12,
  calm: false, visibility_m: 7000, cloud: [{ cover: "BKN", base_ft: 1500 }], cavok: false,
  temp_c: -2, dewpoint_c: -5, qnh_hpa: 1008, raw: "EGKK 141750Z 24012KT 7000 BKN015 M02/M05 Q1008",
};
const render = (weather = metar, connected = true) => renderToStaticMarkup(
  createElement(WeatherPanel, { metar: weather, connected }));

test("scenario weather preserves operational units, negative temperatures and provenance", () => {
  const html = render();
  for (const text of ["SIMULATED", "17:50Z", "240° / 12 kt", "7,000 m", "BKN", "1,500 ft",
    "-2°C / -5°C", "1008 hPa", metar.raw]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("CAVOK"));
});
test("calm and variable winds, CAVOK and multiple cloud layers remain distinct", () => {
  const calm = render({ ...metar, calm: true, wind_dir_deg: null, wind_kt: 0,
    visibility_m: 9999, cavok: true, cloud: [] });
  for (const text of ["Calm", "10 km or more", "CAVOK", "No significant cloud"]) assert.ok(calm.includes(text));
  const variable = render({ ...metar, wind_dir_deg: null, cloud: [...metar.cloud, { cover: "OVC", base_ft: 3000 }] });
  assert.ok(variable.includes("VRB / 12 kt"));
  assert.ok(variable.includes("3,000 ft"));
});
test("missing or disconnected weather never presents old values or a live observation", () => {
  for (const html of [render(null), render(metar, false), render({ ...metar, source: "LIVE" })]) {
    assert.ok(html.includes("UNAVAILABLE"));
    assert.ok(!html.includes(metar.raw));
    assert.ok(!html.includes("1008 hPa"));
  }
  assert.ok(render(metar, false).includes("Reconnect"));
});
