import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

async function compile(path, imports = {}) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  let { outputText } = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } });
  outputText = outputText.replace(/import "\.\/traffic.css";/g, "");
  for (const [name, url] of Object.entries(imports)) {
    outputText = outputText.replaceAll('"' + name + '"', JSON.stringify(url));
  }
  return "data:text/javascript;base64," + Buffer.from(outputText).toString("base64");
}
const geometry = await compile("../src/radar/geometry.ts");
const groupsURL = await compile("../src/ui/trafficGroups.ts");
const { groupTraffic } = await import(groupsURL);
const componentURL = await compile("../src/ui/TrafficPanel.tsx", {
  "../radar/geometry": geometry, "./trafficGroups": groupsURL,
  "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
  "react": import.meta.resolve("react"),
});
const { TrafficPanel } = await import(componentURL);
function ac(callsign, flow, phase) {
  return { callsign, flow, phase, spoken_plain: callsign, actype: "A320",
    wake: "M", alt: 6000, cleared_alt: 4000, gs: 210, route_name: null };
}

test("groups preserve server order and distinguish takeoff from airborne traffic", () => {
  const input = [ac("A", "ARR", "STAR"), ac("B", "DEP", "QUEUED"),
    ac("C", "DEP", "SID"), ac("D", "ARR", "LANDED"),
    ac("E", "DEP", "HANDED_OFF"), ac("F", "DEP", "TAKEOFF")];
  const before = JSON.stringify(input);
  const groups = groupTraffic(input);
  assert.deepEqual(groups.arrivals.map(a => a.callsign), ["A"]);
  assert.deepEqual(groups.queued.map(a => a.callsign), ["B"]);
  assert.deepEqual(groups.departing.map(a => a.callsign), ["F"]);
  assert.deepEqual(groups.airborne.map(a => a.callsign), ["C"]);
  assert.equal(JSON.stringify(input), before);
});
test("flight strips show server values and accessible shared selection", () => {
  const state = { aircraft: [ac("BAW117", "ARR", "STAR")] };
  const html = renderToStaticMarkup(createElement(TrafficPanel, {
    state, selected: "BAW117", onSelect() {},
  }));
  assert.match(html, /aria-pressed="true"/);
  assert.match(html, /A060/); assert.match(html, /A040/); assert.match(html, /210/);
  assert.match(html, /No departures in this session/);
  const cleared = renderToStaticMarkup(createElement(TrafficPanel, {
    state: { aircraft: [] }, selected: "", onSelect() {},
  }));
  assert.doesNotMatch(cleared, /BAW117/);
  assert.match(cleared, /No active arrivals/);
});
test("disconnected state reports waiting instead of empty traffic", () => {
  const html = renderToStaticMarkup(createElement(TrafficPanel, {
    state: null, selected: "", onSelect() {},
  }));
  assert.match(html, /Waiting for aircraft state/);
  assert.doesNotMatch(html, /No active arrivals/);
});
