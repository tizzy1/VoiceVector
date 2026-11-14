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
const { groupTraffic, matchesCallsign } = await import(groupsURL);
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
test("callsign filtering is case-insensitive, trims whitespace and retains phase groups", () => {
  const input = [ac("BAW117", "ARR", "STAR"), ac("EZY9", "ARR", "STAR"),
    ac("BAW2", "DEP", "QUEUED"), ac("BAW4", "DEP", "TAKEOFF"),
    ac("BAW3", "DEP", "SID"), ac("BAW1", "ARR", "LANDED")];
  const before = JSON.stringify(input);
  const groups = groupTraffic(input, " baw ");
  assert.deepEqual(groups.arrivals.map(a => a.callsign), ["BAW117"]);
  assert.deepEqual(groups.queued.map(a => a.callsign), ["BAW2"]);
  assert.deepEqual(groups.departing.map(a => a.callsign), ["BAW4"]);
  assert.deepEqual(groups.airborne.map(a => a.callsign), ["BAW3"]);
  assert.equal(groupTraffic(input, "not present").arrivals.length, 0);
  assert.equal(JSON.stringify(input), before);
  assert.equal(matchesCallsign("BAW117", "117"), true);
  assert.equal(matchesCallsign("BAW117", "ezy"), false);
});
test("sort uses observed altitude and stable callsign ties without changing the simulation array", () => {
  const input = [
    { ...ac("BAW10", "ARR", "STAR"), alt: 7000, cleared_alt: 3000 },
    { ...ac("BAW2", "ARR", "STAR"), alt: 7000, cleared_alt: 9000 },
    { ...ac("EZY1", "ARR", "STAR"), alt: 4000, cleared_alt: 8000 },
    { ...ac("BAW4", "DEP", "QUEUED"), alt: 200 },
  ];
  const before = JSON.stringify(input);
  const order = sort => groupTraffic(input, "", sort).arrivals.map(a => a.callsign);
  assert.deepEqual(order("callsign-asc"), ["BAW2", "BAW10", "EZY1"]);
  assert.deepEqual(order("callsign-desc"), ["EZY1", "BAW10", "BAW2"]);
  assert.deepEqual(order("altitude-asc"), ["EZY1", "BAW2", "BAW10"]);
  assert.deepEqual(order("altitude-desc"), ["BAW2", "BAW10", "EZY1"]);
  assert.deepEqual(order("session"), ["BAW10", "BAW2", "EZY1"]);
  assert.deepEqual(groupTraffic(input, "BAW", "callsign-asc").arrivals.map(a => a.callsign), ["BAW2", "BAW10"]);
  assert.equal(JSON.stringify(input), before);
});
test("list controls expose labelled filtering and all sort modes while preserving selected rows", () => {
  const html = renderToStaticMarkup(createElement(TrafficPanel, {
    state: { aircraft: [ac("BAW117", "ARR", "STAR")] }, selected: "BAW117", onSelect() {},
  }));
  for (const text of ["Filter callsign", 'type="search"', "Sort flights", "Session order",
    "Callsign A–Z", "Callsign Z–A", "Altitude low–high", "Altitude high–low",
    'aria-pressed="true"', "Lists only"]) assert.ok(html.includes(text), text);
});
